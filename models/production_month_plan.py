from datetime import datetime
from sqlalchemy import Column,Integer,String,Float,Boolean,ForeignKey,DateTime,UniqueConstraint,CheckConstraint
from sqlalchemy.orm import relationship
from core.database import Base

class ProductionEquipmentSchedule(Base):
    __tablename__='production_equipment_schedules'
    __table_args__=(UniqueConstraint('equipment_id','work_date',name='uq_equipment_schedule_day'),)
    id=Column(Integer,primary_key=True)
    equipment_id=Column(Integer,ForeignKey('equipment_masters.id'),nullable=False,index=True)
    work_date=Column(String(10),nullable=False,index=True)
    working=Column(Boolean,nullable=False)
    start_time=Column(String(5),nullable=False)
    end_time=Column(String(5),nullable=False)
    next_day=Column(Boolean,nullable=False)
    updated_at=Column(DateTime,default=datetime.now,onupdate=datetime.now,nullable=False)

class ProductionMonthPlanLine(Base):
    __tablename__='production_month_plan_lines'
    __table_args__=(UniqueConstraint('month','equipment_id','item_id',name='uq_month_plan_line'),)
    id=Column(Integer,primary_key=True)
    month=Column(String(7),nullable=False,index=True)
    equipment_id=Column(Integer,ForeignKey('equipment_masters.id'),nullable=False,index=True)
    item_id=Column(Integer,ForeignKey('item_master.id'),nullable=False,index=True)
    cycle_seconds=Column(Float,nullable=False)
    expected_oee=Column(Float,nullable=False)
    updated_at=Column(DateTime,default=datetime.now,onupdate=datetime.now,nullable=False)
    days=relationship('ProductionMonthPlanDay',cascade='all, delete-orphan',back_populates='line',order_by='ProductionMonthPlanDay.work_date')

class ProductionMonthPlanDay(Base):
    __tablename__='production_month_plan_days'
    __table_args__=(UniqueConstraint('line_id','work_date',name='uq_plan_line_date'),CheckConstraint('quantity >= 0 AND cycle_seconds > 0 AND expected_oee > 0 AND expected_oee <= 1',name='ck_month_plan_day_values'))
    id=Column(Integer,primary_key=True)
    line_id=Column(Integer,ForeignKey('production_month_plan_lines.id'),nullable=False,index=True)
    work_date=Column(String(10),nullable=False,index=True)
    quantity=Column(Float,nullable=False,default=0)
    cycle_seconds=Column(Float,nullable=False)
    expected_oee=Column(Float,nullable=False)
    line=relationship('ProductionMonthPlanLine',back_populates='days')

class ProductionPlanWorkTime(Base):
    """Saved shift hours, shared by all item lines on one equipment/date."""
    __tablename__='production_plan_work_times'
    __table_args__=(UniqueConstraint('equipment_id','work_date',name='uq_plan_work_time_day'),)
    id=Column(Integer,primary_key=True)
    equipment_id=Column(Integer,ForeignKey('equipment_masters.id'),nullable=False,index=True)
    work_date=Column(String(10),nullable=False,index=True)
    start_time=Column(String(5),nullable=False,default='08:00')
    end_time=Column(String(5),nullable=False,default='08:00')
    next_day=Column(Boolean,nullable=False,default=True)
