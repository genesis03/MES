from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from core.database import Base


class LotConsumptionModel(Base):
    """생산 공정에서 소비된 LOT 수량 원장.

    구매/생산 LOT의 원 수량은 보존하고, 현재 사용가능수량은 이 소비원장을 차감해 계산합니다.
    """

    __tablename__ = "lot_consumptions"

    id = Column(Integer, primary_key=True)
    lot_no = Column(String(100), nullable=False, index=True)
    part_no = Column(String(80), nullable=False, index=True)
    work_order_id = Column(Integer, ForeignKey("production_work_orders.id"), nullable=False, index=True)
    performance_id = Column(Integer, ForeignKey("production_performances.id"), nullable=False, index=True)
    process_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False, index=True)
    consumed_qty = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
