from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from core.database import Base


class LotRelationModel(Base):
    """공정 LOT 계보(부모 LOT -> 자식 LOT)를 기록합니다."""

    __tablename__ = "lot_relations"

    id = Column(Integer, primary_key=True)
    parent_lot_no = Column(String(100), nullable=False, index=True)
    child_lot_no = Column(String(100), nullable=False, index=True)
    process_code = Column(String(50), nullable=True, index=True)
    consumed_qty = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
