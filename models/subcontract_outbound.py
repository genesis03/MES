from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from core.database import Base


class SubcontractOutboundMaster(Base):
    __tablename__ = "subcontract_outbound_masters"
    __table_args__ = (
        CheckConstraint("status IN ('OUTBOUND','CANCELLED')"),
    )

    id = Column(Integer, primary_key=True)
    outbound_no = Column(String(20), unique=True, nullable=False, index=True)
    outbound_date = Column(String(10), nullable=False, index=True)
    order_id = Column(Integer, ForeignKey("subcontract_order_masters.id"), nullable=False, index=True)
    order_no = Column(String(30), nullable=False, index=True)
    partner_id = Column(Integer, ForeignKey("partners.id"), nullable=False, index=True)
    partner_name = Column(String(100), nullable=False)
    processing_type_code = Column(String(50), nullable=False)
    processing_type_name = Column(String(100), nullable=False)
    external_storage_location = Column(String(20), nullable=False)
    manager_name = Column(String(50), nullable=True)
    status = Column(String(20), nullable=False, default="OUTBOUND")
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    cancelled_by = Column(String(50), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    items = relationship(
        "SubcontractOutboundItem",
        back_populates="outbound",
        cascade="all, delete-orphan",
        order_by="SubcontractOutboundItem.id",
    )


class SubcontractOutboundItem(Base):
    __tablename__ = "subcontract_outbound_items"
    __table_args__ = (CheckConstraint("outbound_qty > 0"),)

    id = Column(Integer, primary_key=True)
    outbound_id = Column(Integer, ForeignKey("subcontract_outbound_masters.id"), nullable=False, index=True)
    order_item_id = Column(Integer, ForeignKey("subcontract_order_items.id"), nullable=False, index=True)
    previous_item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    previous_part_no = Column(String(50), nullable=False, index=True)
    order_part_no = Column(String(80), nullable=False, index=True)
    order_part_name = Column(String(200), nullable=False)
    spec = Column(String(200), nullable=True)
    unit = Column(String(10), nullable=False)
    outbound_qty = Column(Float, nullable=False)
    lot_count = Column(Integer, nullable=False, default=0)
    note = Column(Text, nullable=True)

    outbound = relationship("SubcontractOutboundMaster", back_populates="items")
    lots = relationship(
        "SubcontractOutboundLot",
        back_populates="outbound_item",
        cascade="all, delete-orphan",
        order_by="SubcontractOutboundLot.id",
    )


class SubcontractOutboundLot(Base):
    __tablename__ = "subcontract_outbound_lots"
    __table_args__ = (CheckConstraint("outbound_qty > 0"),)

    id = Column(Integer, primary_key=True)
    outbound_item_id = Column(Integer, ForeignKey("subcontract_outbound_items.id"), nullable=False, index=True)
    allocation_id = Column(Integer, ForeignKey("subcontract_lot_allocations.id"), nullable=True, index=True)
    lot_no = Column(String(100), nullable=False, index=True)
    outbound_qty = Column(Float, nullable=False)

    outbound_item = relationship("SubcontractOutboundItem", back_populates="lots")
