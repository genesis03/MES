from datetime import datetime
from sqlalchemy import Column,Integer,String,Text,DateTime,UniqueConstraint
from core.database import Base

class DailyJobReportSupplement(Base):
    __tablename__='daily_job_report_supplements'
    __table_args__=(UniqueConstraint('record_source','record_id'),)
    id=Column(Integer,primary_key=True)
    record_source=Column(String(20),nullable=False)
    record_id=Column(Integer,nullable=False)
    data_json=Column(Text,nullable=False)
    updated_by=Column(String(50),nullable=False)
    updated_at=Column(DateTime,nullable=False,default=datetime.now,onupdate=datetime.now)
