from collections import defaultdict

from core.database import SessionLocal
from models.production_lot import ProductionLotModel
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster


def repair_subcontract_inbound_sample_stock() -> None:
    """확정된 외주입고 LOT의 최초수량을 실입고수량 기준으로 복원합니다.

    샘플수량은 LOT 사용수량으로 계산하므로 production_lots.lot_qty에는
    최초 입고수량을 유지합니다. 과거 보정으로 줄어든 LOT 수량도 복원합니다.
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
            expected[row.child_lot_no] += float(row.good_qty or 0)

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
