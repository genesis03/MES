"""Immutable inspection results with the standard used at inspection time."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from core.database import Base


class InspectionReportMaster(Base):
    __tablename__ = 'inspection_report_masters'
    id = Column(Integer, primary_key=True)
    report_no = Column(String(50), unique=True, nullable=False, index=True)
    report_type = Column(String(20), nullable=False, default='SHIPPING', index=True)
    item_id = Column(Integer, ForeignKey('item_master.id'), nullable=False, index=True)
    part_no = Column(String(100), nullable=False, index=True)
    part_name = Column(String(200), nullable=False, default='')
    lot_no = Column(String(100), nullable=False, index=True)
    order_no = Column(String(50))
    inspection_date = Column(String(10), nullable=False, index=True)
    inspector_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    inspector_name = Column(String(100), nullable=False)
    standard_id = Column(Integer, ForeignKey('inspection_standards.id'))
    standard_revision = Column(String(20))
    judgment = Column(String(20), nullable=False)
    sample_qty = Column(Integer, nullable=False, default=5)
    note = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    items = relationship('InspectionReportItem', cascade='all, delete-orphan', order_by='InspectionReportItem.item_seq')


class InspectionReportItem(Base):
    __tablename__ = 'inspection_report_items'
    __table_args__ = (UniqueConstraint('report_id', 'item_seq'),)
    id = Column(Integer, primary_key=True)
    report_id = Column(Integer, ForeignKey('inspection_report_masters.id', ondelete='CASCADE'), nullable=False, index=True)
    standard_item_id = Column(Integer, ForeignKey('inspection_standard_items.id'))
    item_seq = Column(Integer, nullable=False)
    item_category = Column(String(100), default='')
    item_name = Column(String(200), nullable=False)
    spec_display = Column(Text, nullable=False, default='')
    spec_min = Column(Float)
    spec_max = Column(Float)
    eval_tool = Column(String(200))
    val_1 = Column(Float)
    val_2 = Column(Float)
    val_3 = Column(Float)
    val_4 = Column(Float)
    val_5 = Column(Float)
    avg_val = Column(Float)
    judgment = Column(String(10), nullable=False)
