from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import (
    ItemMasterModel,
    PurchaseInboundItem,
    PurchaseInboundMaster,
    PurchaseOrderItem,
    PurchaseOrderMaster,
)

router = APIRouter(prefix="/api/purchase/inquiry", tags=["Purchase Inquiry"])


class SelectedIds(BaseModel):
    ids: List[int] = Field(min_length=1, max_length=500)


ORDER_STATUS_NAMES = {
    "ORDERED": "발주완료",
    "PARTIAL": "부분입고",
    "COMPLETED": "입고완료",
    "CANCELLED": "취소",
}


@router.get("/orders")
def inquiry_orders(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    po_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=50),
    status: Optional[str] = Query(None, max_length=20),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = (
        db.query(PurchaseOrderMaster, PurchaseOrderItem, ItemMasterModel)
        .join(PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrderMaster.id)
        .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseOrderItem.part_no)
    )
    if start_date:
        query = query.filter(PurchaseOrderMaster.order_date >= start_date)
    if end_date:
        query = query.filter(PurchaseOrderMaster.order_date <= end_date)
    if po_no:
        query = query.filter(PurchaseOrderMaster.po_no.contains(po_no.strip(), autoescape=True))
    if partner_name:
        query = query.filter(PurchaseOrderMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if part_no:
        query = query.filter(PurchaseOrderItem.part_no.contains(part_no.strip(), autoescape=True))
    if status:
        if status not in ORDER_STATUS_NAMES:
            raise HTTPException(422, "지원하지 않는 발주 상태입니다.")
        query = query.filter(PurchaseOrderMaster.status == status)

    total = query.count()
    rows = (
        query.order_by(PurchaseOrderMaster.order_date.desc(), PurchaseOrderMaster.id.desc(), PurchaseOrderItem.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "po_id": master.id,
                "po_item_id": item.id,
                "po_no": master.po_no,
                "order_date": master.order_date,
                "delivery_due_date": master.delivery_due_date or "",
                "partner_name": master.partner_name,
                "manager_name": master.manager_name or "",
                "part_no": item.part_no,
                "part_name": product.part_name,
                "spec": product.spec or "",
                "order_qty": item.order_qty,
                "unit": item.unit,
                "item_delivery_date": item.delivery_date or "",
                "status": master.status,
                "status_name": ORDER_STATUS_NAMES.get(master.status, master.status),
                "note": item.note or master.note or "",
            }
            for master, item, product in rows
        ],
    }


@router.post("/orders/delete-selected")
def delete_selected_orders(
    payload: SelectedIds,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ids = sorted(set(payload.ids))
    masters = db.query(PurchaseOrderMaster).filter(PurchaseOrderMaster.id.in_(ids)).all()
    found = {row.id for row in masters}
    missing = [value for value in ids if value not in found]
    if missing:
        raise HTTPException(404, f"발주를 찾을 수 없습니다: {', '.join(map(str, missing))}")

    blocked = []
    for master in masters:
        item_ids = [item.id for item in master.items]
        has_receipt = any((item.received_qty or 0) > 0 for item in master.items)
        if item_ids and not has_receipt:
            has_receipt = (
                db.query(PurchaseInboundItem.id)
                .filter(PurchaseInboundItem.po_item_id.in_(item_ids))
                .first()
                is not None
            )
        if has_receipt:
            blocked.append(master.po_no)

    if blocked:
        raise HTTPException(
            409,
            "입고 이력이 있는 발주는 삭제할 수 없습니다: " + ", ".join(blocked),
        )

    for master in masters:
        db.delete(master)
    db.commit()
    return {"deleted": len(masters), "message": f"발주 {len(masters)}건을 삭제했습니다."}


@router.get("/inbounds")
def inquiry_inbounds(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    inbound_no: Optional[str] = Query(None, max_length=30),
    po_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=50),
    lot: Optional[str] = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = (
        db.query(PurchaseInboundMaster, PurchaseInboundItem, PurchaseOrderMaster.po_no, ItemMasterModel)
        .join(PurchaseInboundItem, PurchaseInboundItem.inbound_id == PurchaseInboundMaster.id)
        .outerjoin(PurchaseOrderItem, PurchaseOrderItem.id == PurchaseInboundItem.po_item_id)
        .outerjoin(PurchaseOrderMaster, PurchaseOrderMaster.id == PurchaseOrderItem.po_id)
        .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseInboundItem.part_no)
        .filter(PurchaseInboundMaster.status == "CONFIRMED")
    )
    if start_date:
        query = query.filter(PurchaseInboundMaster.inbound_date >= start_date)
    if end_date:
        query = query.filter(PurchaseInboundMaster.inbound_date <= end_date)
    if inbound_no:
        query = query.filter(PurchaseInboundMaster.inbound_no.contains(inbound_no.strip(), autoescape=True))
    if po_no:
        query = query.filter(PurchaseOrderMaster.po_no.contains(po_no.strip(), autoescape=True))
    if partner_name:
        query = query.filter(PurchaseInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if part_no:
        query = query.filter(PurchaseInboundItem.part_no.contains(part_no.strip(), autoescape=True))
    if lot:
        keyword = lot.strip()
        query = query.filter(or_(
            PurchaseInboundItem.supplier_lot_no.contains(keyword, autoescape=True),
            PurchaseInboundItem.internal_lot_no.contains(keyword, autoescape=True),
        ))

    total = query.count()
    rows = (
        query.order_by(PurchaseInboundMaster.inbound_date.desc(), PurchaseInboundMaster.id.desc(), PurchaseInboundItem.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "inbound_id": master.id,
                "inbound_item_id": item.id,
                "inbound_no": master.inbound_no,
                "inbound_date": master.inbound_date,
                "po_no": linked_po_no or "",
                "partner_name": master.partner_name,
                "part_no": item.part_no,
                "part_name": product.part_name,
                "spec": product.spec or "",
                "inbound_qty": item.inbound_qty,
                "unit": item.unit,
                "supplier_lot_no": item.supplier_lot_no,
                "internal_lot_no": item.internal_lot_no or "",
                "warehouse_code": item.warehouse_code,
                "storage_location": item.storage_location,
                "inspection_status": item.inspection_status,
                "note": item.note or master.note or "",
                "status": master.status,
            }
            for master, item, linked_po_no, product in rows
        ],
    }


@router.post("/inbounds/delete-selected")
def delete_selected_inbounds(
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

    blocked = [row.inbound_no for row in masters if row.status != "DRAFT"]
    if blocked:
        raise HTTPException(
            409,
            "확정된 입고는 LOT/재고 이력 보호를 위해 삭제할 수 없습니다: " + ", ".join(blocked),
        )

    for master in masters:
        db.delete(master)
    db.commit()
    return {"deleted": len(masters), "message": f"임시저장 입고 {len(masters)}건을 삭제했습니다."}
