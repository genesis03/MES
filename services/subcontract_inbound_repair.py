from collections import defaultdict

from core.database import SessionLocal
from models.production_lot import ProductionLotModel
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster


def repair_subcontract_inbound_sample_stock() -> None:
    """확정된 외주입고 LOT의 재고 기준수량에서 샘플 사용수량을 제외합니다.

    과거 데이터도 동일 규칙으로 맞추기 위한 기동 시 보정입니다.
    취소된 입고는 제외하고, LOT별 실입고수량 - 샘플수량 합계로
    production_lots.lot_qty를 정렬합니다.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(SubcontractInboundLot)
            .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
            .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
            .filter(
                SubcontractInboundMaster.status == "RECEIVED",
                SubcontractInboundLot.child_lot_no.isnot(None),
                SubcontractInboundLot.child_lot_no != "",
            )
            .all()
        )

        expected = defaultdict(float)
        for row in rows:
            expected[row.child_lot_no] += max(float(row.good_qty or 0) - float(row.sample_qty or 0), 0.0)

        changed = False
        for lot_no, qty in expected.items():
            stock = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot_no).one_or_none()
            if stock is None:
                continue
            if abs(float(stock.lot_qty or 0) - qty) > 1e-9:
                stock.lot_qty = qty
                changed = True

        if changed:
            db.commit()
        else:
            db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
