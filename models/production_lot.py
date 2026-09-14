from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from core.database import Base


class ProductionLotModel(Base):
    """생산 LOT 재고 원장.

    생산등록 기능이 붙기 전에는 테스트 LOT 입력에도 사용하고,
    이후에는 생산실적 확정 시 동일 테이블에 LOT를 적재할 수 있습니다.
    """

    __tablename__ = "production_lots"

    id = Column(Integer, primary_key=True)
    lot_no = Column(String(100), unique=True, nullable=False, index=True)
    part_no = Column(String(50), nullable=False, index=True)
    lot_qty = Column(Float, nullable=False)
    storage_location = Column(String(20), nullable=True)
    status = Column(String(20), nullable=False, default="ACTIVE", index=True)
    note = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
