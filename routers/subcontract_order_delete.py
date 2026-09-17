from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.subcontract import SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundMaster

router = APIRouter(prefix="/api/subcontract", tags=["Subcontract Order Delete"])


@router.delete("/orders/{order_id}")
def delete_subcontract_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.get(SubcontractOrderMaster, order_id)
    if order is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")

    outbound = (
        db.query(SubcontractOutboundMaster.id, SubcontractOutboundMaster.outbound_no, SubcontractOutboundMaster.status)
        .filter(SubcontractOutboundMaster.order_id == order.id)
        .order_by(SubcontractOutboundMaster.id.desc())
        .first()
    )
    if outbound:
        raise HTTPException(
            409,
            f"외주가공 출고 이력이 있어 발주를 삭제할 수 없습니다. 출고번호: {outbound.outbound_no} ({outbound.status})",
        )

    inbound = (
        db.query(SubcontractInboundMaster.id, SubcontractInboundMaster.inbound_no, SubcontractInboundMaster.status)
        .filter(SubcontractInboundMaster.order_id == order.id)
        .order_by(SubcontractInboundMaster.id.desc())
        .first()
    )
    if inbound:
        raise HTTPException(
            409,
            f"외주가공 입고 이력이 있어 발주를 삭제할 수 없습니다. 입고번호: {inbound.inbound_no} ({inbound.status})",
        )

    order_no = order.order_no
    released_lot_count = sum(len(item.allocations) for item in order.items)
    db.delete(order)
    db.commit()

    return {
        "status": "success",
        "message": f"{order_no} 외주가공 발주를 삭제했습니다.",
        "released_lot_count": released_lot_count,
    }
