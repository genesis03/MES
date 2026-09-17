from core.database import SessionLocal
from models.subcontract import SubcontractLotAllocation, SubcontractOrderMaster
from models.subcontract_outbound import SubcontractOutboundMaster


def repair_cancelled_subcontract_reservations() -> None:
    """과거 출고취소 건에 남은 LOT 예약만 안전하게 해제한다.

    취소 출고 스냅샷이 아직 가리키는 allocation_id만 해제한다.
    이후 사용자가 새로 배정한 LOT까지 건드리지 않는다.
    """
    db = SessionLocal()
    try:
        cancelled_rows = (
            db.query(SubcontractOutboundMaster)
            .filter(SubcontractOutboundMaster.status == "CANCELLED")
            .order_by(SubcontractOutboundMaster.id.asc())
            .all()
        )
        changed = False
        for outbound in cancelled_rows:
            active = (
                db.query(SubcontractOutboundMaster.id)
                .filter(
                    SubcontractOutboundMaster.order_id == outbound.order_id,
                    SubcontractOutboundMaster.status == "OUTBOUND",
                )
                .first()
            )
            if active:
                continue

            allocation_ids = {
                lot.allocation_id
                for item in outbound.items
                for lot in item.lots
                if lot.allocation_id is not None
            }
            if not allocation_ids:
                continue

            for item in outbound.items:
                for lot in item.lots:
                    if lot.allocation_id in allocation_ids:
                        lot.allocation_id = None
            db.flush()

            deleted = (
                db.query(SubcontractLotAllocation)
                .filter(SubcontractLotAllocation.id.in_(allocation_ids))
                .delete(synchronize_session=False)
            )
            order = db.get(SubcontractOrderMaster, outbound.order_id)
            if order is not None and order.status != "CANCELLED" and deleted:
                order.status = "DRAFT"
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
