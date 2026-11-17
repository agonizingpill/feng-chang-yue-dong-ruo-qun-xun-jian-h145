import json,hashlib,uuid
from datetime import datetime
from pathlib import Path
from sqlalchemy import select,func
from fastapi import HTTPException
from .models import Visit
from .reference_models import ReferenceVersion
from .screen_model import train_model,evidence_for,digest,version_model,screen_with
from .yard_service import require,audit
BASELINE_NAME='固定参考 V1 · winter_reference.json'
def ensure_baseline(db):
    row=db.scalar(select(ReferenceVersion).where(ReferenceVersion.is_baseline.is_(True)))
    if row:return row
    raw=(Path(__file__).resolve().parents[1]/'fixtures/winter_reference.json').read_bytes()
    rows=json.loads(raw);raw_sha=hashlib.sha256(raw).hexdigest();content_sha=digest(rows)
    model,x=train_model(rows);evidence=evidence_for(rows,raw_sha,model,x)
    row=ReferenceVersion(name=BASELINE_NAME,source_rows=rows,data_sha256=content_sha,status='active',is_baseline=True,
                         evidence=evidence,active_generation=1,
                         activated_by='system',note='随应用初始化的固定参考，巡检证据中的历史分值均以此版本训练')
    db.add(row);db.commit();return row
def active_version(db,lock=False):
    q=select(ReferenceVersion).where(ReferenceVersion.status=='active')
    if lock:q=q.with_for_update()
    row=db.scalar(q)
    if not row:return ensure_baseline(db)
    return row
def summary(row):
    return {'id':row.id,'name':row.name,'status':row.status,'is_baseline':row.is_baseline,'data_sha256':row.data_sha256,
            'evidence':row.evidence,'comparison':row.comparison,'active_generation':row.active_generation,
            'activated_by':row.activated_by,'activated_at':row.activated_at,'created_by':row.created_by,
            'created_at':row.created_at,'updated_at':row.updated_at,'note':row.note,'row_count':len(row.source_rows or [])}
def list_versions(db):
    return [summary(r) for r in db.scalars(select(ReferenceVersion).order_by(ReferenceVersion.id)).all()]
def upload_candidate(db,body,user):
    rows=[r.model_dump() for r in body.rows]
    if db.scalar(select(ReferenceVersion).where(ReferenceVersion.name==body.name)):
        raise HTTPException(409,'参考版本名称已存在，请换一个名称')
    sha=digest(rows)
    dup=db.scalar(select(ReferenceVersion).where(ReferenceVersion.data_sha256==sha))
    if dup:raise HTTPException(409,f'候选参考数据与版本「{dup.name}」完全相同，无需重复上传')
    try:
        model,x=train_model(rows);evidence=evidence_for(rows,sha,model,x)
    except Exception as error:
        raise HTTPException(422,f'候选参考训练失败，在用版本保持不变：{error}')
    row=ReferenceVersion(name=body.name,source_rows=rows,data_sha256=sha,status='candidate',
                         evidence=evidence,note=body.note,created_by=user.email)
    db.add(row);audit(db,user,f'上传候选参考 {body.name}（{len(rows)}条），训练完成待对照');db.commit();return summary(row)
def update_candidate(db,key,body,user):
    row=require(db,ReferenceVersion,key,True)
    if row.status not in ['candidate','failed']:raise HTTPException(409,'仅候选（或训练失败）版本允许替换源数据，在用与历史版本冻结')
    rows=[r.model_dump() for r in body.rows];sha=digest(rows)
    try:
        model,x=train_model(rows);evidence=evidence_for(rows,sha,model,x)
    except Exception as error:
        raise HTTPException(422,f'候选参考训练失败，已保留上一版数据：{error}')
    row.source_rows=rows;row.data_sha256=sha;row.evidence=evidence;row.status='candidate'
    row.comparison=None;row.note=body.note
    audit(db,user,f'候选参考 {row.name} 源数据更新，原对照失效须重新试算');db.commit();return summary(row)
def latest_screenable(db):
    ids=select(func.max(Visit.id)).group_by(Visit.hive_id)
    return db.scalars(select(Visit).where(Visit.id.in_(ids),Visit.status.in_(['recorded','screened','blocked'])).order_by(Visit.id)).all()
def start_comparison(db,key,user,queue):
    row=require(db,ReferenceVersion,key,True)
    if row.status not in ['candidate','failed']:raise HTTPException(409,'只有候选版本可以发起对照试算')
    current=active_version(db)
    visits=latest_screenable(db)
    if not visits:raise HTTPException(409,'没有可对照的最新巡检，请先登记读数')
    jobid=uuid.uuid4().hex
    row.status='candidate'
    row.comparison={'job_id':jobid,'status':'queued','active_version_id':current.id,'active_sha256':current.data_sha256,
                    'candidate_sha256':row.data_sha256,'queued_by':user.email,'count':len(visits)}
    audit(db,user,f'候选参考 {row.name} 对在用「{current.name}」发起 {len(visits)} 箱对照试算');db.commit()
    queue.set('apiary:reference:'+jobid,json.dumps({'status':'queued'}),ex=86400)
    queue.rpush('apiary:reference_jobs',json.dumps({'job_id':jobid,'version_id':row.id,'visit_ids':[v.id for v in visits],
                                                     'active_version_id':current.id,'active_sha256':current.data_sha256,
                                                     'candidate_sha256':row.data_sha256}))
    return {'job_id':jobid,'version_id':row.id,'count':len(visits)}
def write_comparison(db,version_id,jobid,payload,status='done',error=None):
    row=db.get(ReferenceVersion,version_id)
    if not row or not row.comparison or row.comparison.get('job_id')!=jobid:return
    if status=='done':
        row.comparison={**row.comparison,'status':'done',**payload};row.status='candidate'
    else:
        row.comparison={**row.comparison,'status':'failed','error':error or '对照计算失败'};row.status='failed'
    db.commit()
def activate(db,key,user,queue):
    row=require(db,ReferenceVersion,key,True)
    current=active_version(db,lock=True)
    if row.id==current.id:raise HTTPException(409,f'「{row.name}」已是在用版本，无需重复启用')
    if row.status not in ['candidate','failed']:raise HTTPException(409,'仅候选参考版本可以启用')
    comp=row.comparison
    if not comp or comp.get('status')!='done':raise HTTPException(409,'该候选尚未完成与在用版本的对照，不能启用')
    if comp.get('active_version_id')!=current.id or comp.get('active_sha256')!=current.data_sha256 or comp.get('candidate_sha256')!=row.data_sha256:
        row.comparison=None
        db.commit()
        raise HTTPException(409,f'启用冲突：候选「{row.name}」对照后在用参考已变更（或候选源数据已更新），原对照失效，须重新试算')
    queued=queue.lrange('apiary:screening',0,-1)
    generation=current.active_generation+1
    current.status='retired'
    row.status='active';row.active_generation=generation;row.activated_by=user.email
    row.activated_at=datetime.utcnow()
    audit(db,user,f'启用候选参考 {row.name}（第{generation}代），原在用 {current.name} 转历史；切换前排队筛查 {len(queued)} 个仍按入队版本');db.commit()
    return summary(row)
def rollback(db,key,user):
    row=require(db,ReferenceVersion,key,True)
    current=active_version(db,lock=True)
    if row.id==current.id:raise HTTPException(409,f'「{row.name}」已是在用版本')
    if row.status not in ['retired','active']:raise HTTPException(409,'只能回退到历史在用版本，候选版本须先完成对照并启用')
    if row.evidence is None:raise HTTPException(409,'该版本缺少训练依据，不能回退')
    generation=current.active_generation+1
    current.status='retired'
    row.status='active';row.active_generation=generation;row.activated_by=user.email
    row.activated_at=datetime.utcnow()
    audit(db,user,f'回退参考版本到 {row.name}（第{generation}代），既有巡检分值与处置依据保留原版本不动');db.commit()
    return summary(row)
def score_visits(version,visits):
    model,evidence=version_model(version.id,version.data_sha256,version.source_rows)
    out=[]
    for v in visits:
        score,flag,margin=screen_with(model,evidence,v)
        out.append({'visit_id':v.id,'hive_id':v.hive_id,'code':v.code,'score':score,'flag':flag,'margin':margin,
                    'queen_seen':v.queen_seen,'version_id':version.id,'version_name':version.name})
    return out,evidence
