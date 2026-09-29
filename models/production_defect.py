from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from core.database import Base


class QualityProductionDefect(Base):
    __tablename__ = "quality_production_defects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    production_lot_id = Column(Integer, ForeignKey("production_lots.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    lot_no = Column(String(100), nullable=False, index=True)
    lot_qty = Column(Float, nullable=False, default=0.0)
    available_qty_before = Column(Float, nullable=False, default=0.0)
    defect_date = Column(String(10), nullable=False, index=True)
    defect_qty = Column(Float, nullable=False, default=0.0)
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)
    remark = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    cancelled_by = Column(String(100), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(Text, nullable=True)


class QualityProductionDefectDetail(Base):
    __tablename__ = "quality_production_defect_details"

    id = Column(Integer, primary_key=True, autoincrement=True)
    defect_id = Column(
        Integer,
        ForeignKey("quality_production_defects.id"),
        nullable=False,
        index=True,
    )
    defect_type_code = Column(String(30), nullable=False, index=True)
    defect_qty = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
