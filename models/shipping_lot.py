from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from core.database import Base


class ShippingLotRegistry(Base):
    """포장 LOT(=출고 LOT) 공용 발번 레지스트리.

    업무 유일성은 품번 + LOT 번호 조합입니다. 서로 다른 품번은 같은 LOT 번호를 사용할 수 있습니다.
    """

    __tablename__ = "shipping_lot_registry"
    __table_args__ = (
        UniqueConstraint("part_no", "lot_no", name="uq_shipping_lot_part_lot"),
        UniqueConstraint("part_no", "lot_date", "sequence", name="uq_shipping_lot_part_date_seq"),
    )

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    part_no = Column(String(50), nullable=False, index=True)
    lot_no = Column(String(60), nullable=False, index=True)
    lot_date = Column(String(10), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    source_type = Column(String(20), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
