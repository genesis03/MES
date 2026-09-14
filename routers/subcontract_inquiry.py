from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.subcontract import SubcontractOrderItem, SubcontractOrderMaster

router = APIRouter(prefix="/api/subcontract/inquiry", tags=["Subcontract Inquiry"])


class SelectedIds(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)


STATUS_NAMES = {
    "DRAFT": "작성중",
    "LOT_ALLOCATING": "LOT배정중",
    "ORDERED": "발주완료",
    "CANCELLED": "취소",
}


@router.get("/orders")
def inquiry_subcontract_orders(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    po_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=80),
    status: Optional[str] = Query(None, max_length=20),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = (
        db.query(SubcontractOrderMaster, SubcontractOrderItem)
        .join(SubcontractOrderItem, SubcontractOrderItem.order_id == SubcontractOrderMaster.id)
    )
    if start_date:
        query = query.filter(SubcontractOrderMaster.order_date >= start_date)
    if end_date:
        query = query.filter(SubcontractOrderMaster.order_date <= end_date)
    if po_no:
        query = query.filter(SubcontractOrderMaster.order_no.contains(po_no.strip(), autoescape=True))
    if partner_name:
        query = query.filter(SubcontractOrderMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if part_no:
        keyword = part_no.strip()
        query = query.filter(
            (SubcontractOrderItem.order_part_no.contains(keyword, autoescape=True))
            | (SubcontractOrderItem.previous_part_no.contains(keyword, autoescape=True))
        )
    if status and status in STATUS_NAMES:
        query = query.filter(SubcontractOrderMaster.status == status)

    total = query.count()
    rows = (
        query.order_by(SubcontractOrderMaster.order_date.desc(), SubcontractOrderMaster.id.desc(), SubcontractOrderItem.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = []
    for master, item in rows:
        allocated_qty = sum(float(x.allocated_qty or 0) for x in item.allocations)
        items.append({
            "type": "SUBCONTRACT",
            "po_id": master.id,
            "po_item_id": item.id,
            "po_no": master.order_no,
            "order_date": master.order_date,
            "delivery_due_date": master.delivery_due_date or "",
            "partner_name": master.partner_name,
            "manager_name": master.manager_name or "",
            "part_no": item.order_part_no,
            "previous_part_no": item.previous_part_no,
            "part_name": item.order_part_name,
            "spec": item.spec or "",
            "order_qty": item.order_qty,
            "unit": item.unit,
            "item_delivery_date": item.delivery_date or "",
            "status": master.status,
            "status_name": STATUS_NAMES.get(master.status, master.status),
            "note": item.note or master.note or "",
            "allocated_qty": allocated_qty,
        })
    return {"total": total, "items": items}


@router.post("/orders/delete-selected")
def delete_selected_subcontract_orders(
    payload: SelectedIds,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ids = sorted(set(payload.ids))
    masters = db.query(SubcontractOrderMaster).filter(SubcontractOrderMaster.id.in_(ids)).all()
    found = {row.id for row in masters}
    missing = [value for value in ids if value not in found]
    if missing:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다: " + ", ".join(map(str, missing)))

    # LOT 배정 자체는 삭제를 막지 않습니다. 발주 삭제 시 cascade로 배정도 함께 해제됩니다.
    # 단, 배정된 LOT가 이미 다음 공정에서 소비된 이력이 있으면 추적성 보호를 위해 삭제를 막습니다.
    blocked = []
    for master in masters:
        allocated_lots = sorted({
            allocation.lot_no
            for item in master.items
            for allocation in item.allocations
            if allocation.lot_no
        })
        if not allocated_lots:
            continue
        used = (
            db.query(LotRelationModel.parent_lot_no)
            .filter(LotRelationModel.parent_lot_no.in_(allocated_lots))
            .distinct()
            .all()
        )
        if used:
            blocked.append(master.order_no)

    if blocked:
        raise HTTPException(
            409,
            "다음 공정에서 이미 사용된 LOT가 있는 외주가공 발주는 삭제할 수 없습니다: " + ", ".join(blocked),
        )

    for master in masters:
        db.delete(master)
    db.commit()
    return {
        "deleted": len(masters),
        "message": f"외주가공 발주 {len(masters)}건을 삭제했습니다. 연결된 LOT 배정도 함께 해제했습니다.",
    }
