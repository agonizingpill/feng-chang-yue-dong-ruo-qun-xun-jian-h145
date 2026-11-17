from pydantic import BaseModel,Field,field_validator
FEATURES=['weight_kg','temperature','activity','feed_left']
class ReferenceRow(BaseModel):
    weight_kg:float=Field(gt=0,le=100)
    temperature:float=Field(ge=-10,le=50)
    activity:float=Field(ge=0,le=500)
    feed_left:float=Field(ge=0,le=20)
class ReferenceUpload(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    rows:list[ReferenceRow]=Field(min_length=20,max_length=5000)
    note:str=Field(default='',max_length=300)
    @field_validator('name')
    @classmethod
    def clean_name(cls,v):return v.strip()
class ReferenceUpdate(BaseModel):
    rows:list[ReferenceRow]=Field(min_length=20,max_length=5000)
    note:str=Field(default='',max_length=300)
class ActivateInput(BaseModel):
    reason:str=Field(default='',max_length=300)
