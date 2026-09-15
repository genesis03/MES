from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import PurchaseInboundItem, PurchaseInboundMaster, PurchaseOrderItem
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster

router = APIRouter(prefix="/api/purchase/inquiry", tags=["Purchase Delete Guard"])


class SelectedIds(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)


def _recalculate_order_status(order):
    if order.status == "CANCELLED":
        return
    if all((item.received_qty or 0) >= item.order_qty for item in order.items):
        order.status = "COMPLETED"
    elif any((item.received_qty or 0) > 0 for item in order.items):
        order.status = "PARTIAL"
    else:
        order.status = "ORDERED"


def _used_purchase_lots(db: Session, lot_nos: list[str]) -> list[str]:
    if not lot_nos:
        return []

    used = set()
    # 완료된 생산실적에서 실제 소비된 원자재 LOT
    used.update(
        row[0]
        for row in db.query(LotConsumptionModel.lot_no)
        .filter(LotConsumptionModel.lot_no.in_(lot_nos))
        .distinct()
        .all()
    )
    # 공정/외주 LOT 계보에 이미 사용된 LOT
    used.update(
        row[0]
        for row in db.query(LotRelationModel.parent_lot_no)
        .filter(LotRelationModel.parent_lot_no.in_(lot_nos))
        .distinct()
        .all()
    )
    # 아직 종료 전인 생산 가동내역에 배정된 LOT도 삭제 금지
    used.update(
        row[0]
        for row in (
            db.query(ProductionRunLotAllocation.lot_no)
            .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
            .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
            .filter(
                ProductionRunLotAllocation.lot_no.in_(lot_nos),
                ProductionRun.status.in_(["IN_PROGRESS", "COMPLETED"]),
            )
            .distinct()
            .all()
        )
    )
    # 외주가공 발주에 예약/사용된 LOT도 원본 구매입고를 삭제할 수 없음
    used.update(
        row[0]
        for row in (
            db.query(SubcontractLotAllocation.lot_no)
            .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
            .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
            .filter(
                SubcontractLotAllocation.lot_no.in_(lot_nos),
                SubcontractOrderMaster.status != "CANCELLED",
            )
            .distinct()
            .all()
        )
    )
    return sorted(value for value in used if value)


@router.post("/inbounds/delete-selected")
def guarded_delete_selected_inbounds(
    payload: SelectedIds,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ids = sorted(set(payload.ids))
    masters = db.query(PurchaseInboundMaster).filter(PurchaseInboundMaster.id.in_(ids)).all()
    found = {row.id for row in masters}
    missing = [value for value in ids if value not in found]
    if missing:
        raise HTTPException(404, f"입고를 찾을 수 없습니다: {', '.join(map(str, missing))}")

    confirmed_lots = sorted({
        item.internal_lot_no
        for master in masters if master.status == "CONFIRMED"
        for item in master.items if item.internal_lot_no
    })
    used_lots = _used_purchase_lots(db, confirmed_lots)
    if used_lots:
        raise HTTPException(
            409,
            "생산실적/다음 공정에서 사용 중인 원자재 LOT가 있어 구매입고를 삭제할 수 없습니다. "
            "먼저 연결된 생산실적을 삭제하세요: " + ", ".join(used_lots),
        )

    affected_orders = {}
    for master in masters:
        if master.status != "CONFIRMED":
            continue
        for inbound_item in master.items:
            if inbound_item.po_item_id is None:
                continue
            po_item = db.get(PurchaseOrderItem, inbound_item.po_item_id)
            if po_item is None:
                continue
            received = Decimal(str(po_item.received_qty or 0)) - Decimal(str(inbound_item.inbound_qty or 0))
            po_item.received_qty = float(max(received, Decimal("0")))
            if po_item.received_qty <= 0:
                po_item.status = "WAITING"
            elif po_item.received_qty >= po_item.order_qty:
                po_item.status = "COMPLETED"
            else:
                po_item.status = "PARTIAL"
            affected_orders[po_item.order.id] = po_item.order

    for order in affected_orders.values():
        _recalculate_order_status(order)

    for master in masters:
        db.delete(master)
    db.commit()
    return {
        "deleted": len(masters),
        "message": f"구매 {len(masters)}건을 삭제했습니다. 확정 입고는 발주 입고수량과 상태도 함께 복구했습니다.",
    }
