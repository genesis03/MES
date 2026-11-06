"""Independent monthly quality-report manual values and their audit history."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,Text,DateTime
from core.database import Base


class QualityPerformanceMonth(Base):
    __tablename__='quality_performance_months'
    id=Column(Integer,primary_key=True)
    month=Column(String(7),nullable=False,unique=True,index=True)
    version=Column(Integer,nullable=False,default=1)
    data_json=Column(Text,nullable=False)
    updated_by=Column(String(100),nullable=False)
    updated_at=Column(DateTime,nullable=False,default=datetime.now)


class QualityPerformanceAudit(Base):
    __tablename__='quality_performance_audits'
    id=Column(Integer,primary_key=True)
    month=Column(String(7),nullable=False,index=True)
    before_json=Column(Text,nullable=False)
    after_json=Column(Text,nullable=False)
    actor=Column(String(100),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.now)
