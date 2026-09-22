from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Text,
    DateTime,
    ForeignKey,
    Index
)
from sqlalchemy.orm import relationship

from core.database import Base


class PurchaseMaster(Base):
    """
    구매(입고) 전표 헤더 모델
    """
    __tablename__ = "purchase_masters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_no = Column(String(30), unique=True, nullable=False, index=True)  # 구매 전표번호 (예: PU-20260910-001)
    purchase_date = Column(String(10), nullable=False, index=True)            # 구매/입고일자 (YYYY-MM-DD)
    
    # 거래처(구매처/공통) 참조
    partner_id = Column(Integer, ForeignKey("partners.id", ondelete="RESTRICT"), nullable=True, index=True)
    partner_name = Column(String(100), nullable=False)                        # 거래처명 (비정규화)
    
    status = Column(String(20), nullable=False, default="COMPLETED")          # COMPLETED(구매확정), CANCEL(취소)
    manager_name = Column(String(50), nullable=True)                          # 구매/입고 담당자
    remark = Column(Text, nullable=True)                                      # 전표 비고

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    # 관계 설정
    partner = relationship("Partner", lazy="selectin")
    items = relationship(
        "PurchaseItem",
        back_populates="purchase_master",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class PurchaseItem(Base):
    """
    구매(입고) 품목 상세 모델 (단가 및 공급가액 포함)
    """
    __tablename__ = "purchase_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    purchase_id = Column(
        Integer,
        ForeignKey("purchase_masters.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    part_no = Column(String(50), nullable=False, index=True)       # 당시/호환 품번
    part_name = Column(String(100), nullable=False)                # 품명
    lot_no = Column(String(50), nullable=True, index=True)         # 입고 LOT 번호 / 바코드
    qty = Column(Float, nullable=False, default=0.0)               # 구매(입고) 수량
    unit = Column(String(10), nullable=False, default="EA")        # 단위
    unit_price = Column(Float, nullable=False, default=0.0)        # 구매 단가
    supply_price = Column(Float, nullable=False, default=0.0)      # 공급가액 (수량 * 단가)
    
    location_code = Column(String(30), nullable=True)              # 적재 창고/저장위치 코드
    note = Column(String(200), nullable=True)                      # 행별 비고

    created_at = Column(DateTime, nullable=False, default=datetime.now)

    purchase_master = relationship("PurchaseMaster", back_populates="items")


# 복합 인덱스
Index("ix_purchase_date_status", PurchaseMaster.purchase_date, PurchaseMaster.status)