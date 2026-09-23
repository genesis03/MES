from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from core.database import Base


class SubcontractInboundMaster(Base):
    __tablename__ = "subcontract_inbound_masters"
    __table_args__ = (
        CheckConstraint("status IN ('RECEIVED','CANCELLED')"),
    )

    id = Column(Integer, primary_key=True)
    inbound_no = Column(String(20), unique=True, nullable=False, index=True)
    inbound_date = Column(String(10), nullable=False, index=True)
    outbound_id = Column(Integer, ForeignKey("subcontract_outbound_masters.id"), nullable=False, index=True)
    outbound_no = Column(String(20), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("subcontract_order_masters.id"), nullable=False, index=True)
    order_no = Column(String(30), nullable=False, index=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=False, index=True)
    partner_name = Column(String(100), nullable=False)
    processing_type_code = Column(String(50), nullable=False)
    processing_type_name = Column(String(100), nullable=False)
    storage_location = Column(String(20), nullable=False)
    manager_name = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="RECEIVED")
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    cancelled_by = Column(String(50), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    items = relationship(
        "SubcontractInboundItem",
        back_populates="inbound",
        cascade="all, delete-orphan",
        order_by="SubcontractInboundItem.id",
    )


class SubcontractInboundItem(Base):
    __tablename__ = "subcontract_inbound_items"
    __table_args__ = (
        CheckConstraint("outbound_qty > 0"),
        CheckConstraint("good_qty >= 0 AND defect_qty >= 0"),
    )

    id = Column(Integer, primary_key=True)
    inbound_id = Column(Integer, ForeignKey("subcontract_inbound_masters.id"), nullable=False, index=True)
    outbound_item_id = Column(Integer, ForeignKey("subcontract_outbound_items.id"), nullable=False, index=True)
    order_item_id = Column(Integer, ForeignKey("subcontract_order_items.id"), nullable=False, index=True)
    previous_item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    previous_part_no = Column(String(50), nullable=False, index=True)
    part_no = Column(String(80), nullable=False, index=True)
    part_name = Column(String(200), nullable=False)
    spec = Column(String(200), nullable=True)
    unit = Column(String(10), nullable=False)
    outbound_qty = Column(Float, nullable=False)
    # 기존 컬럼은 DB 호환을 위해 유지합니다. 외주입고에서는 good_qty=실입고수량, defect_qty=0으로 사용합니다.
    good_qty = Column(Float, nullable=False)
    defect_qty = Column(Float, nullable=False, default=0)
    note = Column(Text, nullable=True)

    inbound = relationship("SubcontractInboundMaster", back_populates="items")
    lots = relationship(
        "SubcontractInboundLot",
        back_populates="inbound_item",
        cascade="all, delete-orphan",
        order_by="SubcontractInboundLot.id",
    )


class SubcontractInboundLot(Base):
    __tablename__ = "subcontract_inbound_lots"
    __table_args__ = (
        CheckConstraint("source_qty > 0"),
        CheckConstraint("good_qty >= 0 AND defect_qty >= 0"),
    )

    id = Column(Integer, primary_key=True)
    inbound_item_id = Column(Integer, ForeignKey("subcontract_inbound_items.id"), nullable=False, index=True)
    outbound_lot_id = Column(Integer, ForeignKey("subcontract_outbound_lots.id"), nullable=False, index=True)
    source_lot_no = Column(String(100), nullable=False, index=True)
    # source_qty는 원 출고 LOT 배정수량, good_qty는 금회 실입고수량으로 사용합니다.
    source_qty = Column(Float, nullable=False)
    good_qty = Column(Float, nullable=False)
    defect_qty = Column(Float, nullable=False, default=0)
    defect_type = Column(String(20), nullable=True)
    child_lot_no = Column(String(100), nullable=True, index=True)
    supplier_lot_no = Column(String(100), nullable=True, index=True)
    sample_qty = Column(Float, nullable=False, default=0)

    inbound_item = relationship("SubcontractInboundItem", back_populates="lots")
