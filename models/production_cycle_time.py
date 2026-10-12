from datetime import datetime
from sqlalchemy import Column, Integer, Float, ForeignKey, DateTime, CheckConstraint
from core.database import Base


class ProductionCycleTime(Base):
    __tablename__ = 'production_cycle_times'
    __table_args__ = (CheckConstraint('seconds > 0', name='ck_cycle_time_positive'),)
    item_id = Column(Integer, ForeignKey('item_master.id'), primary_key=True)
    seconds = Column(Float, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
