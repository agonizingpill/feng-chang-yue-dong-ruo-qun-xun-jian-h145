from datetime import datetime
from sqlalchemy import String,Integer,JSON,DateTime,func
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class ReferenceVersion(Base):
    __tablename__='reference_versions'
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(80),unique=True)
    source_rows:Mapped[list]=mapped_column(JSON)
    data_sha256:Mapped[str]=mapped_column(String(64))
    status:Mapped[str]=mapped_column(String(24),default='candidate')
    is_baseline:Mapped[bool]=mapped_column(default=False)
    evidence:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    comparison:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    active_generation:Mapped[int]=mapped_column(Integer,default=0)
    activated_by:Mapped[str]=mapped_column(String(120),default='')
    activated_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    created_by:Mapped[str]=mapped_column(String(120),default='')
    created_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now())
    updated_at:Mapped[datetime]=mapped_column(DateTime,server_default=func.now(),onupdate=func.now())
    note:Mapped[str]=mapped_column(String(300),default='')
