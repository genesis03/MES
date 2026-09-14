from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
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
INBOUND_STATUS_NAMES = {
    "DRAFT": "임시저장",
    "CONFIRMED": "입고확정",
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
        raise HTTPException(409, "입고 이력이 있는 발주는 삭제할 수 없습니다: " + ", ".join(blocked))

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
    status: Optional[str] = Query(None, max_length=20),
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
    if status:
        if status not in INBOUND_STATUS_NAMES:
            raise HTTPException(422, "지원하지 않는 구매 상태입니다.")
        query = query.filter(PurchaseInboundMaster.status == status)

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
                "source_type": "GENERAL",
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
                "status_name": INBOUND_STATUS_NAMES.get(master.status, master.status),
            }
            for master, item, linked_po_no, product in rows
        ],
    }


def _summary_text(values):
    unique = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in unique:
            unique.append(text)
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return f"{unique[0]} 외 {len(unique) - 1}건"


@router.get("/subcontract-inbounds")
def inquiry_subcontract_inbounds(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    inbound_no: Optional[str] = Query(None, max_length=30),
    po_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=80),
    lot: Optional[str] = Query(None, max_length=100),
    status: Optional[str] = Query(None, max_length=20),
    limit: int = Query(1000, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from models.quality import QualityInboundResult
    from models.subcontract_inbound import SubcontractInboundMaster, SubcontractInboundItem, SubcontractInboundLot

    # 먼저 검색조건에 맞는 입고번호를 찾고, 화면에는 입고번호 1건당 1행만 반환합니다.
    match_query = (
        db.query(SubcontractInboundMaster.id)
        .join(SubcontractInboundItem, SubcontractInboundItem.inbound_id == SubcontractInboundMaster.id)
        .join(SubcontractInboundLot, SubcontractInboundLot.inbound_item_id == SubcontractInboundItem.id)
    )
    if start_date:
        match_query = match_query.filter(SubcontractInboundMaster.inbound_date >= start_date)
    if end_date:
        match_query = match_query.filter(SubcontractInboundMaster.inbound_date <= end_date)
    if inbound_no:
        match_query = match_query.filter(SubcontractInboundMaster.inbound_no.contains(inbound_no.strip(), autoescape=True))
    if po_no:
        match_query = match_query.filter(SubcontractInboundMaster.order_no.contains(po_no.strip(), autoescape=True))
    if partner_name:
        match_query = match_query.filter(SubcontractInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if part_no:
        match_query = match_query.filter(SubcontractInboundItem.part_no.contains(part_no.strip(), autoescape=True))
    if lot:
        keyword = lot.strip()
        match_query = match_query.filter(or_(
            SubcontractInboundLot.source_lot_no.contains(keyword, autoescape=True),
            SubcontractInboundLot.supplier_lot_no.contains(keyword, autoescape=True),
            SubcontractInboundLot.child_lot_no.contains(keyword, autoescape=True),
        ))
    if status:
        if status == "CONFIRMED":
            match_query = match_query.filter(SubcontractInboundMaster.status == "RECEIVED")
        elif status == "DRAFT":
            return {"total": 0, "items": []}
        elif status == "CANCELLED":
            match_query = match_query.filter(SubcontractInboundMaster.status == "CANCELLED")
        else:
            raise HTTPException(422, "지원하지 않는 외주입고 상태입니다.")

    matched_ids = [row[0] for row in match_query.distinct().limit(limit).all()]
    if not matched_ids:
        return {"total": 0, "items": []}

    masters = (
        db.query(SubcontractInboundMaster)
        .filter(SubcontractInboundMaster.id.in_(matched_ids))
        .order_by(SubcontractInboundMaster.inbound_date.desc(), SubcontractInboundMaster.id.desc())
        .all()
    )

    items = []
    for master in masters:
        inbound_items = list(master.items)
        inbound_item_ids = [inbound_item.id for inbound_item in inbound_items]
        quality_rows = []
        if inbound_item_ids:
            quality_rows = (
                db.query(QualityInboundResult)
                .filter(
                    QualityInboundResult.source_type == "SUBCONTRACT",
                    QualityInboundResult.inbound_item_id.in_(inbound_item_ids),
                )
                .all()
            )
        completed_ids = {
            row.inbound_item_id for row in quality_rows
            if row.inspection_status == "COMPLETED"
        }
        if inbound_item_ids and len(completed_ids) == len(inbound_item_ids):
            inspection_status = "검사완료"
        elif completed_ids:
            inspection_status = "일부완료"
        else:
            inspection_status = "검사대기"

        lots = [lot_row for inbound_item in inbound_items for lot_row in inbound_item.lots]
        is_received = master.status == "RECEIVED"
        part_nos = [inbound_item.part_no for inbound_item in inbound_items]
        part_names = [inbound_item.part_name for inbound_item in inbound_items]
        specs = [inbound_item.spec for inbound_item in inbound_items]
        units = [inbound_item.unit for inbound_item in inbound_items]
        supplier_lots = [lot_row.supplier_lot_no for lot_row in lots]
        internal_lots = [lot_row.child_lot_no or lot_row.source_lot_no for lot_row in lots]
        source_lots = [lot_row.source_lot_no for lot_row in lots]
        notes = [inbound_item.note for inbound_item in inbound_items if inbound_item.note]
        total_good_qty = sum(float(inbound_item.good_qty or 0) for inbound_item in inbound_items)
        total_sample_qty = sum(float(lot_row.sample_qty or 0) for lot_row in lots)

        items.append({
            "source_type": "SUBCONTRACT",
            "inbound_id": master.id,
            "inbound_item_id": inbound_items[0].id if inbound_items else None,
            "inbound_no": master.inbound_no,
            "inbound_date": master.inbound_date,
            "po_no": master.order_no,
            "partner_name": master.partner_name,
            "part_no": _summary_text(part_nos),
            "part_name": _summary_text(part_names),
            "spec": _summary_text(specs),
            "inbound_qty": total_good_qty,
            "unit": _summary_text(units),
            "supplier_lot_no": _summary_text(supplier_lots),
            "internal_lot_no": _summary_text(internal_lots),
            "warehouse_code": "",
            "storage_location": master.storage_location,
            "inspection_status": inspection_status,
            "note": master.note or _summary_text(notes),
            "status": "CONFIRMED" if is_received else "CANCELLED",
            "status_name": "입고확정" if is_received else "입고취소",
            "source_lot_no": _summary_text(source_lots),
            "source_lot_count": len({value for value in source_lots if value}),
            "sample_qty": total_sample_qty,
            "processing_type_name": master.processing_type_name,
        })
    return {"total": len(items), "items": items}


def _recalculate_order_status(order):
    if order.status == "CANCELLED":
        return
    if all((item.received_qty or 0) >= item.order_qty for item in order.items):
        order.status = "COMPLETED"
    elif any((item.received_qty or 0) > 0 for item in order.items):
        order.status = "PARTIAL"
    else:
        order.status = "ORDERED"


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

    confirmed_lots = sorted({
        item.internal_lot_no
        for master in masters if master.status == "CONFIRMED"
        for item in master.items if item.internal_lot_no
    })
    used_lots = []
    if confirmed_lots:
        used_lots = [row[0] for row in db.query(LotRelationModel.parent_lot_no).filter(
            LotRelationModel.parent_lot_no.in_(confirmed_lots)
        ).distinct().all()]
    if used_lots:
        raise HTTPException(
            409,
            "다음 공정에서 이미 사용된 LOT가 있어 구매를 삭제할 수 없습니다: " + ", ".join(sorted(used_lots)),
        )

    affected_orders = {}
    for master in masters:
        if master.status == "CONFIRMED":
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
