from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, UniqueConstraint

from core.database import Base


class QualityInboundResult(Base):
    __tablename__ = "quality_inbound_results"
    __table_args__ = (
        UniqueConstraint("source_type", "inbound_item_id", name="uq_quality_inbound_source_item"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(20), nullable=False, index=True)  # GENERAL / SUBCONTRACT
    inbound_id = Column(Integer, nullable=False, index=True)
    inbound_item_id = Column(Integer, nullable=False, index=True)
    inspection_status = Column(String(20), nullable=False, default="WAITING")
    defect_qty = Column(Float, nullable=False, default=0.0)
    defect_type_code = Column(String(30), nullable=True, index=True)
    judgment = Column(String(20), nullable=True)
    remark = Column(Text, nullable=True)
    updated_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
