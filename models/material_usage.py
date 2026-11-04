"""Independent handwritten material-use records. No operational stock foreign keys."""
from datetime import datetime
from sqlalchemy import Column,Integer,String,Text,DateTime,Boolean
from core.database import Base


class MaterialUsageRecord(Base):
    __tablename__='material_usage_records'
    id=Column(Integer,primary_key=True)
    request_key=Column(String(80),nullable=False,unique=True)
    work_date=Column(String(10),nullable=False,index=True)
    worker=Column(String(100),nullable=False)
    part_no=Column(String(100),nullable=False,index=True)
    data_json=Column(Text,nullable=False)
    version=Column(Integer,nullable=False,default=1)
    cancelled=Column(Boolean,nullable=False,default=False,index=True)
    created_by=Column(String(100),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.now)
    updated_by=Column(String(100),nullable=False)
    updated_at=Column(DateTime,nullable=False,default=datetime.now)


class MaterialUsageAudit(Base):
    __tablename__='material_usage_audits'
    id=Column(Integer,primary_key=True)
    record_id=Column(Integer,nullable=False,index=True)
    action=Column(String(20),nullable=False)
    before_json=Column(Text,nullable=False)
    after_json=Column(Text,nullable=False)
    reason=Column(String(1000),nullable=False)
    actor=Column(String(100),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.now)
