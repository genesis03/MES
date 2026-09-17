from core.database import SessionLocal
from models.subcontract import SubcontractOrderMaster
from models.subcontract_outbound import SubcontractOutboundMaster


def _release_order_allocations(db, order: SubcontractOrderMaster) -> int:
    released = 0
    for item in order.items:
        released += len(item.allocations)
        item.allocations.clear()
    if order.status != "CANCELLED":
        order.status = "DRAFT"
    return released


def repair_cancelled_subcontract_reservations() -> None:
    """기존 출고취소 건에 남은 LOT 예약을 안전하게 해제한다.

    출고가 취소됐고 같은 외주발주에 현재 OUTBOUND 상태 출고가 없을 때만
    출고 스냅샷의 allocation_id 연결을 끊고 발주 LOT 배정을 해제한다.
    출고 이력 자체는 삭제하지 않는다.
    """
    db = SessionLocal()
    try:
        cancelled_order_ids = {
            row[0]
            for row in (
                db.query(SubcontractOutboundMaster.order_id)
                .filter(SubcontractOutboundMaster.status == "CANCELLED")
                .distinct()
                .all()
            )
        }
        changed = False
        for order_id in cancelled_order_ids:
            active = (
                db.query(SubcontractOutboundMaster.id)
                .filter(
                    SubcontractOutboundMaster.order_id == order_id,
                    SubcontractOutboundMaster.status == "OUTBOUND",
                )
                .first()
            )
            if active:
                continue

            order = db.get(SubcontractOrderMaster, order_id)
            if order is None:
                continue

            cancelled_rows = (
                db.query(SubcontractOutboundMaster)
                .filter(
                    SubcontractOutboundMaster.order_id == order_id,
                    SubcontractOutboundMaster.status == "CANCELLED",
                )
                .all()
            )
            for outbound in cancelled_rows:
                for item in outbound.items:
                    for lot in item.lots:
                        if lot.allocation_id is not None:
                            lot.allocation_id = None
                            changed = True
            db.flush()

            if any(item.allocations for item in order.items):
                _release_order_allocations(db, order)
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
