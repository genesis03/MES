from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.security import get_current_user
from models.models import ItemMasterModel, PurchaseOrderMaster
from routers.purchase import get_purchase_db
from schemas.purchase import OrderCreate, OrderOut
from services.purchase_service import update_order

router = APIRouter(prefix="/api/purchase", tags=["Purchase Order Edit"])


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
        "editable": all((item.received_qty or 0) == 0 for item in master.items) and master.status != "CANCELLED",
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
