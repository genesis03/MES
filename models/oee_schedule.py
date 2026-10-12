from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, UniqueConstraint
from core.database import Base


class OeeScheduleDay(Base):
    __tablename__='oee_schedule_days'
    __table_args__=(UniqueConstraint('process_code','work_date',name='uq_oee_schedule_process_day'),)
    id=Column(Integer,primary_key=True)
    process_code=Column(String(50),ForeignKey('processes.process_code'),nullable=False,index=True)
    work_date=Column(String(10),nullable=False,index=True)
    working=Column(Boolean,nullable=False,default=False)
    start_time=Column(String(5),nullable=False,default='08:00')
    end_time=Column(String(5),nullable=False,default='08:00')
    next_day=Column(Boolean,nullable=False,default=True)
    updated_at=Column(DateTime,nullable=False,default=datetime.now,onupdate=datetime.now)
