from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base


class SalesOrderMaster(Base):
    __tablename__ = "sales_order_masters"
    __table_args__ = (CheckConstraint("status IN ('ORDERED','PARTIAL','COMPLETED','CANCELLED')"),)

    id = Column(Integer, primary_key=True)
    order_no = Column(String(30), unique=True, nullable=False, index=True)
    order_date = Column(String(10), nullable=False, index=True)
    delivery_due_date = Column(String(10), nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("partners.id"), nullable=False, index=True)
    customer_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="ORDERED", index=True)
    # 수주 목적과 거래 조건은 분리한다. 샘플/개발도 유상일 수 있으므로 한 필드로 합치지 않는다.
    order_type = Column(String(20), nullable=False, default="NORMAL", index=True)  # NORMAL/SAMPLE/DEVELOPMENT
    transaction_type = Column(String(20), nullable=False, default="PAID", index=True)  # PAID/FREE
    manager_name = Column(String(50), nullable=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    items = relationship("SalesOrderItem", back_populates="order", cascade="all, delete-orphan", order_by="SalesOrderItem.id")


class SalesOrderItem(Base):
    __tablename__ = "sales_order_items"
    __table_args__ = (
        CheckConstraint("order_qty > 0 AND shipped_qty >= 0"),
        CheckConstraint("status IN ('WAITING','PARTIAL','COMPLETED')"),
    )

    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("sales_order_masters.id"), nullable=False, index=True)
    part_no = Column(String(50), ForeignKey("item_master.part_no"), nullable=False, index=True)
    part_name = Column(String(200), nullable=True)
    order_qty = Column(Float, nullable=False)
    shipped_qty = Column(Float, nullable=False, default=0.0)
    unit = Column(String(10), nullable=False, default="EA")
    delivery_date = Column(String(10), nullable=True)
    note = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="WAITING", index=True)

    order = relationship("SalesOrderMaster", back_populates="items")
    shipment_items = relationship("ShipmentItem", back_populates="sales_order_item")


class ShipmentMaster(Base):
    __tablename__ = "shipment_masters"

    id = Column(Integer, primary_key=True)
    shipment_no = Column(String(30), unique=True, nullable=False, index=True)
    shipment_date = Column(String(10), nullable=False, index=True)
    sales_order_id = Column(Integer, ForeignKey("sales_order_masters.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("partners.id"), nullable=False, index=True)
    customer_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default="CONFIRMED", index=True)
    fifo_exception = Column(String(1), nullable=False, default="N", index=True)
    fifo_exception_reason = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    items = relationship("ShipmentItem", back_populates="shipment", cascade="all, delete-orphan", order_by="ShipmentItem.id")


class ShipmentItem(Base):
    __tablename__ = "shipment_items"

    id = Column(Integer, primary_key=True)
    shipment_id = Column(Integer, ForeignKey("shipment_masters.id"), nullable=False, index=True)
    sales_order_item_id = Column(Integer, ForeignKey("sales_order_items.id"), nullable=False, index=True)
    part_no = Column(String(50), nullable=False, index=True)
    shipped_qty = Column(Float, nullable=False)
    unit = Column(String(10), nullable=False, default="EA")

    shipment = relationship("ShipmentMaster", back_populates="items")
    sales_order_item = relationship("SalesOrderItem", back_populates="shipment_items")
    boxes = relationship("ShipmentBox", back_populates="shipment_item", cascade="all, delete-orphan", order_by="ShipmentBox.id")


class ShipmentBox(Base):
    __tablename__ = "shipment_boxes"
    __table_args__ = (UniqueConstraint("packing_box_id", name="uq_shipment_packing_box"),)

    id = Column(Integer, primary_key=True)
    shipment_item_id = Column(Integer, ForeignKey("shipment_items.id"), nullable=False, index=True)
    packing_box_id = Column(Integer, ForeignKey("packing_boxes.id"), nullable=False, index=True)
    package_lot_no = Column(String(60), nullable=False, index=True)
    shipped_qty = Column(Float, nullable=False)

    shipment_item = relationship("ShipmentItem", back_populates="boxes")
