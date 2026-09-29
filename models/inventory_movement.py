from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from core.database import Base


class InventoryMovementModel(Base):
    __tablename__ = "inventory_movements"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    part_no = Column(String(80), nullable=False, index=True)
    lot_no = Column(String(100), nullable=False, index=True)
    from_location = Column(String(20), nullable=True, index=True)
    to_location = Column(String(20), nullable=False, index=True)
    moved_qty = Column(Float, nullable=False)
    reason = Column(String(100), nullable=False)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
