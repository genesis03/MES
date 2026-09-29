from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from core.database import Base


class InventoryAdjustmentModel(Base):
    __tablename__ = "inventory_adjustments"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    part_no = Column(String(80), nullable=False, index=True)
    lot_no = Column(String(100), nullable=False, index=True)
    storage_location = Column(String(20), nullable=True, index=True)
    before_qty = Column(Float, nullable=False)
    adjustment_qty = Column(Float, nullable=False)
    after_qty = Column(Float, nullable=False)
    reason = Column(String(100), nullable=False)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
