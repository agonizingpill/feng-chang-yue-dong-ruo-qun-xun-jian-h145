import os,json,traceback
from pathlib import Path
import redis
from sqlalchemy import select
from .database import Session
from .models import Visit,Hive
from .reference_models import ReferenceVersion
from .reference_service import write_comparison,score_visits
from .screen_model import version_model,screen_with
from .yard_service import latest
queue=redis.Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379/0'),decode_responses=True)
def run_screen(task):
    points=[]
    with Session() as db:
        ref=db.get(ReferenceVersion,task.get('model_version_id'))
        if not ref:
            raise RuntimeError('入队参考版本已不存在，筛查中止')
        model,evidence=version_model(ref.id,ref.data_sha256,ref.source_rows)
        evidence={**evidence,'model_version_id':ref.id,'model_version_name':ref.name,'model_generation':ref.active_generation}
    for key in task['visit_ids']:
        Path('/tmp/apiary-heartbeat').touch()
        with Session() as db:
            visit=db.scalar(select(Visit).where(Visit.id==key).with_for_update())
            if not visit or visit.status!='screening':continue
            score,flag,margin=screen_with(model,evidence,visit)
            visit.anomaly_score=score;visit.anomaly_flag=flag;visit.decision_margin=margin;visit.model_evidence=evidence;visit.status='screened'
            hive=db.get(Hive,visit.hive_id)
            if latest(db,hive.id)==visit.id:hive.care_state='needs_care' if flag or not visit.queen_seen else 'observe'
            db.commit();points.append({'visit_id':visit.id,'hive_id':hive.id,'score':score,'flag':flag})
    result={'status':'done','count':len(points),'points':points,'evidence':evidence,
            'model_version_id':task.get('model_version_id'),'model_version_name':task.get('model_version_name')}
    Path('/artifacts').mkdir(exist_ok=True);Path(f"/artifacts/screen-{task['job_id']}.json").write_text(json.dumps(result))
    queue.set('apiary:screen:'+task['job_id'],json.dumps(result),ex=86400)
def run_reference(task):
    with Session() as db:
        candidate=db.get(ReferenceVersion,task['version_id'])
        active=db.get(ReferenceVersion,task['active_version_id'])
        if not candidate or not active:raise RuntimeError('参考版本记录缺失')
        if candidate.data_sha256!=task['candidate_sha256'] or active.data_sha256!=task['active_sha256']:
            raise RuntimeError('候选或在用参考源数据在对照期间发生变化，本次对照作废，请重新试算')
        visits=db.scalars(select(Visit).where(Visit.id.in_(task['visit_ids'])).order_by(Visit.id)).all()
        old_points,old_ev=score_visits(active,visits)
        new_points,new_ev=score_visits(candidate,visits)
    old_by={p['visit_id']:p for p in old_points};new_by={p['visit_id']:p for p in new_points}
    items=[];incoming=[];outgoing=[]
    for vid in task['visit_ids']:
        o,n=old_by[vid],new_by[vid]
        kind='same'
        if not o['flag'] and n['flag']:kind='incoming'
        elif o['flag'] and not n['flag']:kind='outgoing'
        item={'visit_id':vid,'hive_id':o['hive_id'],'code':o['code'],'queen_seen':o['queen_seen'],
              'active_score':o['score'],'candidate_score':n['score'],'active_flag':o['flag'],'candidate_flag':n['flag'],
              'active_margin':o['margin'],'candidate_margin':n['margin'],'change':kind}
        items.append(item)
        if kind=='incoming':incoming.append(item)
        if kind=='outgoing':outgoing.append(item)
    payload={'count':len(items),
             'incoming_count':len(incoming),'outgoing_count':len(outgoing),
             'active_version_id':active.id,'active_version_name':active.name,'active_evidence':old_ev,
             'candidate_version_id':candidate.id,'candidate_version_name':candidate.name,'candidate_evidence':new_ev,
             'incoming':incoming,'outgoing':outgoing,'items':items}
    Path('/artifacts').mkdir(exist_ok=True);Path(f"/artifacts/reference-{task['job_id']}.json").write_text(json.dumps(payload,ensure_ascii=False))
    queue.set('apiary:reference:'+task['job_id'],json.dumps({'status':'done',**payload},ensure_ascii=False),ex=86400)
    with Session() as db:write_comparison(db,task['version_id'],task['job_id'],payload)
def main():
    while True:
        Path('/tmp/apiary-heartbeat').touch()
        job=queue.blpop(['apiary:screening','apiary:reference_jobs'],timeout=3)
        if not job:continue
        queue_name,payload=job;task=json.loads(payload)
        try:
            if queue_name=='apiary:screening':
                run_screen(task)
            else:
                run_reference(task)
        except Exception as error:
            traceback.print_exc()
            if queue_name=='apiary:screening':
                queue.set('apiary:screen:'+task['job_id'],json.dumps({'status':'failed','error':str(error)}),ex=86400)
                with Session() as db:
                    for v in db.scalars(select(Visit).where(Visit.id.in_(task['visit_ids']),Visit.status=='screening')).all():v.status='blocked'
                    db.commit()
            else:
                queue.set('apiary:reference:'+task['job_id'],json.dumps({'status':'failed','error':str(error)},ensure_ascii=False),ex=86400)
                with Session() as db:write_comparison(db,task['version_id'],task['job_id'],{},status='failed',error=str(error))
if __name__=='__main__':
    main()
