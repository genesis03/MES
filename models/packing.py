from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base


class PackingMaster(Base):
    __tablename__ = "packing_masters"

    id = Column(Integer, primary_key=True)
    packing_no = Column(String(30), unique=True, nullable=False, index=True)
    packing_date = Column(String(10), nullable=False, index=True)
    part_no = Column(String(50), nullable=False, index=True)
    part_name = Column(String(200), nullable=True)
    box_count = Column(Integer, nullable=False)
    box_qty = Column(Float, nullable=False)
    total_qty = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, default="PACKED", index=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    cancelled_by = Column(String(50), nullable=True)
    cancelled_at = Column(DateTime, nullable=True)

    allocations = relationship(
        "PackingLotAllocation",
        back_populates="master",
        cascade="all, delete-orphan",
        order_by="PackingLotAllocation.id",
    )
    boxes = relationship(
        "PackingBox",
        back_populates="master",
        cascade="all, delete-orphan",
        order_by="PackingBox.box_no",
    )


class PackingLotAllocation(Base):
    __tablename__ = "packing_lot_allocations"
    __table_args__ = (UniqueConstraint("packing_id", "source_lot_no", name="uq_packing_source_lot"),)

    id = Column(Integer, primary_key=True)
    packing_id = Column(Integer, ForeignKey("packing_masters.id"), nullable=False, index=True)
    source_lot_no = Column(String(100), nullable=False, index=True)
    allocated_qty = Column(Float, nullable=False)
    storage_location = Column(String(20), nullable=True)

    master = relationship("PackingMaster", back_populates="allocations")


class PackingBox(Base):
    __tablename__ = "packing_boxes"

    id = Column(Integer, primary_key=True)
    packing_id = Column(Integer, ForeignKey("packing_masters.id"), nullable=False, index=True)
    box_no = Column(Integer, nullable=False)
    # 포장 LOT(=출고 LOT)는 품번과 함께 식별합니다. 다른 품번은 같은 LOT 번호를 사용할 수 있습니다.
    package_lot_no = Column(String(60), nullable=False, index=True)
    box_qty = Column(Float, nullable=False)

    master = relationship("PackingMaster", back_populates="boxes")
