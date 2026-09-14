from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.security import get_current_user
from models.models import ItemMasterModel, PurchaseInboundMaster, PurchaseOrderItem, PurchaseOrderMaster
from routers.purchase import get_purchase_db
from schemas.purchase import InboundCreate, InboundOut, OrderCreate, OrderOut
from services.purchase_service import update_inbound, update_order

router = APIRouter(prefix="/api/purchase", tags=["Purchase Edit"])


@router.get("/orders/{po_id}")
def get_order_for_edit(
    po_id: int,
    db: Session = Depends(get_purchase_db),
    current_user=Depends(get_current_user),
):
    master = db.get(PurchaseOrderMaster, po_id)
    if master is None:
        raise HTTPException(404, "발주를 찾을 수 없습니다.")

    part_nos = {item.part_no for item in master.items}
    parts = {
        row.part_no: row
        for row in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(part_nos)).all()
    }
    editable = all((item.received_qty or 0) == 0 for item in master.items) and master.status != "CANCELLED"
    return {
        "id": master.id,
        "po_no": master.po_no,
        "order_date": master.order_date,
        "delivery_due_date": master.delivery_due_date or "",
        "partner_id": master.partner_id,
        "partner_name": master.partner_name,
        "manager_name": master.manager_name or "",
        "status": master.status,
        "note": master.note or "",
        "editable": editable,
        "items": [
            {
                "id": item.id,
                "part_no": item.part_no,
                "part_name": parts[item.part_no].part_name if item.part_no in parts else "",
                "spec": (parts[item.part_no].spec or "") if item.part_no in parts else "",
                "unit": item.unit,
                "order_qty": item.order_qty,
                "delivery_date": item.delivery_date or "",
                "warehouse_code": item.warehouse_code or "",
                "storage_location": item.storage_location or "",
                "note": item.note or "",
                "received_qty": item.received_qty,
            }
            for item in master.items
        ],
    }


@router.put("/orders/{po_id}", response_model=OrderOut)
def revise_order(
    po_id: int,
    payload: OrderCreate,
    db: Session = Depends(get_purchase_db),
    current_user=Depends(get_current_user),
):
    return update_order(db, po_id, payload)


@router.get("/inbounds/{inbound_id}")
def get_inbound_for_edit(
    inbound_id: int,
    db: Session = Depends(get_purchase_db),
    current_user=Depends(get_current_user),
):
    master = db.get(PurchaseInboundMaster, inbound_id)
    if master is None:
        raise HTTPException(404, "구매 입력을 찾을 수 없습니다.")

    items = []
    for row in master.items:
        po_item = db.get(PurchaseOrderItem, row.po_item_id) if row.po_item_id else None
        part = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == row.part_no).one_or_none()
        order = po_item.order if po_item else None
        editable_remaining = None
        if po_item is not None:
            editable_remaining = po_item.order_qty - po_item.received_qty
            if master.status == "CONFIRMED":
                editable_remaining += row.inbound_qty
        items.append({
            "inbound_item_id": row.id,
            "po_item_id": row.po_item_id,
            "po_no": order.po_no if order else "",
            "part_no": row.part_no,
            "part_name": part.part_name if part else "",
            "spec": (part.spec or "") if part else "",
            "unit": row.unit,
            "order_qty": po_item.order_qty if po_item else None,
            "remaining_qty": editable_remaining,
            "delivery_date": po_item.delivery_date if po_item else "",
            "inbound_qty": row.inbound_qty,
            "supplier_lot_no": row.supplier_lot_no,
            "internal_lot_no": row.internal_lot_no or "",
            "warehouse_code": row.warehouse_code,
            "storage_location": row.storage_location,
            "note": row.note or "",
        })

    first_order = None
    if master.items and master.items[0].po_item_id:
        first_po_item = db.get(PurchaseOrderItem, master.items[0].po_item_id)
        first_order = first_po_item.order if first_po_item else None

    return {
        "id": master.id,
        "inbound_no": master.inbound_no,
        "inbound_date": master.inbound_date,
        "partner_id": master.partner_id,
        "partner_name": master.partner_name,
        "manager_name": first_order.manager_name if first_order else "",
        "invoice_no": master.invoice_no or "",
        "status": master.status,
        "note": master.note or "",
        "items": items,
    }


@router.put("/inbounds/{inbound_id}", response_model=InboundOut)
def revise_inbound(
    inbound_id: int,
    payload: InboundCreate,
    db: Session = Depends(get_purchase_db),
    current_user=Depends(get_current_user),
):
    return update_inbound(db, inbound_id, payload, allow_confirmed=True)
