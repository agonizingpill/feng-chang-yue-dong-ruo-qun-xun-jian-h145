import json,hashlib
from pathlib import Path
from functools import lru_cache
import numpy as np
from sklearn.ensemble import IsolationForest
FEATURES=['weight_kg','temperature','activity','feed_left']
N_ESTIMATORS=150
CONTAMINATION=.15
RANDOM_STATE=42
def train_model(rows):
    x=np.array([[r[k] for k in FEATURES] for r in rows])
    model=IsolationForest(n_estimators=N_ESTIMATORS,contamination=CONTAMINATION,random_state=RANDOM_STATE,n_jobs=1)
    model.fit(x)
    return model,x
def evidence_for(rows,sha,model=None,x=None):
    if model is None or x is None:model,x=train_model(rows)
    scores=model.score_samples(x)
    return {'reference_rows':len(rows),'contamination':CONTAMINATION,'threshold':float(model.offset_),'reference_min':float(scores.min()),'reference_max':float(scores.max()),'reference_flagged':int((scores<model.offset_).sum()),'sha256':sha,'random_state':RANDOM_STATE,'method':f'IsolationForest / {N_ESTIMATORS} trees / score_samples','features':FEATURES}
def digest(rows):
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
_model_cache={}
def version_model(version_id,data_sha,rows):
    key=f'{version_id}:{data_sha}'
    cached=_model_cache.get(key)
    if cached is None:
        model,x=train_model(rows)
        cached=(model,evidence_for(rows,data_sha,model,x))
        _model_cache[key]=cached
        if len(_model_cache)>16:_model_cache.pop(next(iter(_model_cache)))
    return cached
def screen_with(model,evidence,visit):
    score=float(model.score_samples([[getattr(visit,k) for k in FEATURES]])[0])
    return score,score<evidence['threshold'],score-evidence['threshold']
@lru_cache(maxsize=1)
def reference_model():
    raw=(Path(__file__).resolve().parents[1]/'fixtures/winter_reference.json').read_bytes();rows=json.loads(raw)
    model,x=train_model(rows)
    evidence=evidence_for(rows,hashlib.sha256(raw).hexdigest(),model,x)
    return model,evidence
def screen(visit):
    model,evidence=reference_model()
    return screen_with(model,evidence,visit)
