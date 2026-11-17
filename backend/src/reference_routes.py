import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException
from sqlalchemy import select
from .database import session
from .auth import current,editor,admin
from .yard_service import require
from .reference_models import ReferenceVersion
from .reference_schemas import ReferenceUpload,ReferenceUpdate,ActivateInput
from . import reference_service
router=APIRouter()
queue=redis.Redis.from_url(os.getenv('REDIS_URL','redis://redis:6379/0'),decode_responses=True)
@router.get('/reference-versions')
def versions(user=Depends(current),db=Depends(session)):return reference_service.list_versions(db)
@router.get('/reference-versions/active')
def active(user=Depends(current),db=Depends(session)):return reference_service.summary(reference_service.active_version(db))
@router.get('/reference-versions/{key}')
def version(key:int,user=Depends(current),db=Depends(session)):return reference_service.summary(require(db,ReferenceVersion,key))
@router.post('/reference-versions')
def upload(body:ReferenceUpload,user=Depends(editor),db=Depends(session)):return reference_service.upload_candidate(db,body,user)
@router.put('/reference-versions/{key}')
def replace(key:int,body:ReferenceUpdate,user=Depends(editor),db=Depends(session)):return reference_service.update_candidate(db,key,body,user)
@router.post('/reference-versions/{key}/compare')
def compare(key:int,user=Depends(editor),db=Depends(session)):return reference_service.start_comparison(db,key,user,queue)
@router.get('/reference-jobs/{jobid}')
def job(jobid:str,user=Depends(current)):
    result=queue.get('apiary:reference:'+jobid)
    if not result:raise HTTPException(404,'对照结果已过期或不存在，请重新发起试算')
    return json.loads(result)
@router.post('/reference-versions/{key}/activate')
def activate(key:int,body:ActivateInput,user=Depends(admin),db=Depends(session)):return reference_service.activate(db,key,user,queue)
@router.post('/reference-versions/{key}/rollback')
def rollback(key:int,body:ActivateInput,user=Depends(admin),db=Depends(session)):return reference_service.rollback(db,key,user)
