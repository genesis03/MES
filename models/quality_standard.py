from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base


class QualityInspectionItemMaster(Base):
    __tablename__ = "quality_inspection_item_master"
    __table_args__ = (
        UniqueConstraint("item_code", name="uq_quality_inspection_item_code"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_code = Column(String(30), nullable=False, index=True)
    item_name = Column(String(100), nullable=False)
    inspection_method = Column(String(100), nullable=True)
    default_unit = Column(String(20), nullable=True)
    data_type = Column(String(20), nullable=False, default="TEXT")  # TEXT / NUMBER / PASSFAIL
    sort_order = Column(Integer, nullable=False, default=1)
    is_active = Column(String(1), nullable=False, default="Y")
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class QualityInboundStandard(Base):
    __tablename__ = "quality_inbound_standards"
    __table_args__ = (
        UniqueConstraint("part_no", "revision", name="uq_quality_inbound_standard_part_revision"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    part_no = Column(String(80), ForeignKey("item_master.part_no"), nullable=False, index=True)
    revision = Column(String(20), nullable=False, default="Rev.00")
    effective_date = Column(String(10), nullable=True)
    is_active = Column(String(1), nullable=False, default="Y", index=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    items = relationship(
        "QualityInboundStandardItem",
        back_populates="standard",
        cascade="all, delete-orphan",
        order_by="QualityInboundStandardItem.sort_order, QualityInboundStandardItem.id",
    )


class QualityInboundStandardItem(Base):
    __tablename__ = "quality_inbound_standard_items"
    __table_args__ = (
        UniqueConstraint("standard_id", "inspection_item_id", name="uq_quality_standard_inspection_item"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_id = Column(Integer, ForeignKey("quality_inbound_standards.id", ondelete="CASCADE"), nullable=False, index=True)
    inspection_item_id = Column(Integer, ForeignKey("quality_inspection_item_master.id"), nullable=False, index=True)
    spec_type = Column(String(20), nullable=False, default="TEXT")  # TEXT / RANGE / MIN / MAX / TARGET / PASSFAIL
    spec_text = Column(String(300), nullable=True)
    nominal_value = Column(Float, nullable=True)
    lower_limit = Column(Float, nullable=True)
    upper_limit = Column(Float, nullable=True)
    unit = Column(String(20), nullable=True)
    sample_qty = Column(Integer, nullable=False, default=1)
    required_yn = Column(String(1), nullable=False, default="Y")
    sort_order = Column(Integer, nullable=False, default=1)
    note = Column(Text, nullable=True)

    standard = relationship("QualityInboundStandard", back_populates="items")
    inspection_item = relationship("QualityInspectionItemMaster")
