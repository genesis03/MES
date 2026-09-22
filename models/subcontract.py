from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base


class SubcontractOrderMaster(Base):
    __tablename__ = "subcontract_order_masters"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','LOT_ALLOCATING','ORDERED','CANCELLED')"),
    )

    id = Column(Integer, primary_key=True)
    order_no = Column(String(30), unique=True, nullable=False, index=True)
    order_date = Column(String(10), nullable=False, index=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=False, index=True)
    partner_name = Column(String(100), nullable=False)
    processing_type_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False)
    processing_type_name = Column(String(100), nullable=False)
    delivery_due_date = Column(String(10), nullable=True)
    external_storage_location = Column(String(20), ForeignKey("storage_locations.location_code"), nullable=False)
    manager_name = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="DRAFT")
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    items = relationship(
        "SubcontractOrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="SubcontractOrderItem.id",
    )


class SubcontractOrderItem(Base):
    __tablename__ = "subcontract_order_items"
    __table_args__ = (
        CheckConstraint("order_qty > 0"),
    )

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("subcontract_order_masters.id"), nullable=False, index=True)
    previous_item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    previous_part_no = Column(String(50), nullable=False, index=True)
    order_part_no = Column(String(80), nullable=False, index=True)
    order_part_name = Column(String(200), nullable=False)
    spec = Column(String(200), nullable=True)
    unit = Column(String(10), nullable=False)
    processing_type_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False)
    processing_type_name = Column(String(100), nullable=False)
    order_qty = Column(Float, nullable=False)
    delivery_date = Column(String(10), nullable=True)
    note = Column(Text, nullable=True)

    order = relationship("SubcontractOrderMaster", back_populates="items")
    allocations = relationship(
        "SubcontractLotAllocation",
        back_populates="order_item",
        cascade="all, delete-orphan",
        order_by="SubcontractLotAllocation.id",
    )


class SubcontractLotAllocation(Base):
    __tablename__ = "subcontract_lot_allocations"
    __table_args__ = (
        CheckConstraint("lot_qty > 0 AND allocated_qty > 0"),
        UniqueConstraint("order_item_id", "lot_no", name="uq_subcontract_item_lot"),
    )

    id = Column(Integer, primary_key=True)
    order_item_id = Column(Integer, ForeignKey("subcontract_order_items.id"), nullable=False, index=True)
    lot_no = Column(String(100), nullable=False, index=True)
    lot_qty = Column(Float, nullable=False)
    allocated_qty = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    order_item = relationship("SubcontractOrderItem", back_populates="allocations")
