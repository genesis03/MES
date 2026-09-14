from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel, PurchaseOrderItem, PurchaseOrderMaster
from models.subcontract import SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_outbound import SubcontractOutboundMaster

router = APIRouter(prefix="/api/purchase", tags=["Purchase Unreceived"])


@router.get("/unreceived")
def unreceived_list(
    kind: str = Query("ALL", pattern="^(ALL|GENERAL|SUBCONTRACT)$"),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=80),
    order_no: Optional[str] = Query(None, max_length=30),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = []

    if kind in ("ALL", "GENERAL"):
        query = (
            db.query(PurchaseOrderMaster, PurchaseOrderItem, ItemMasterModel)
            .join(PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrderMaster.id)
            .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseOrderItem.part_no)
            .filter(PurchaseOrderMaster.status != "CANCELLED")
            .filter(PurchaseOrderItem.received_qty < PurchaseOrderItem.order_qty)
        )
        if partner_name:
            query = query.filter(PurchaseOrderMaster.partner_name.contains(partner_name.strip(), autoescape=True))
        if part_no:
            query = query.filter(PurchaseOrderItem.part_no.contains(part_no.strip(), autoescape=True))
        if order_no:
            query = query.filter(PurchaseOrderMaster.po_no.contains(order_no.strip(), autoescape=True))

        for master, item, product in query.order_by(PurchaseOrderMaster.order_date.desc(), PurchaseOrderMaster.id.desc(), PurchaseOrderItem.id).all():
            order_qty = float(item.order_qty or 0)
            received_qty = float(item.received_qty or 0)
            remaining_qty = max(order_qty - received_qty, 0.0)
            if remaining_qty <= 0:
                continue
            rows.append({
                "type": "GENERAL",
                "type_name": "일반구매",
                "order_id": master.id,
                "order_no": master.po_no,
                "order_date": master.order_date,
                "due_date": item.delivery_date or master.delivery_due_date or "",
                "partner_name": master.partner_name,
                "part_no": item.part_no,
                "part_name": product.part_name,
                "spec": product.spec or "",
                "unit": item.unit,
                "order_qty": order_qty,
                "received_qty": received_qty,
                "remaining_qty": remaining_qty,
                "stage": "부분입고" if received_qty > 0 else "미입고",
                "note": item.note or master.note or "",
            })

    if kind in ("ALL", "SUBCONTRACT"):
        query = (
            db.query(SubcontractOrderMaster, SubcontractOrderItem)
            .join(SubcontractOrderItem, SubcontractOrderItem.order_id == SubcontractOrderMaster.id)
            .filter(SubcontractOrderMaster.status == "ORDERED")
        )
        if partner_name:
            query = query.filter(SubcontractOrderMaster.partner_name.contains(partner_name.strip(), autoescape=True))
        if part_no:
            keyword = part_no.strip()
            query = query.filter(
                SubcontractOrderItem.order_part_no.contains(keyword, autoescape=True)
                | SubcontractOrderItem.previous_part_no.contains(keyword, autoescape=True)
            )
        if order_no:
            query = query.filter(SubcontractOrderMaster.order_no.contains(order_no.strip(), autoescape=True))

        for master, item in query.order_by(SubcontractOrderMaster.order_date.desc(), SubcontractOrderMaster.id.desc(), SubcontractOrderItem.id).all():
            active_outbound = (
                db.query(SubcontractOutboundMaster)
                .filter(
                    SubcontractOutboundMaster.order_id == master.id,
                    SubcontractOutboundMaster.status == "OUTBOUND",
                )
                .order_by(SubcontractOutboundMaster.id.desc())
                .first()
            )
            order_qty = float(item.order_qty or 0)
            rows.append({
                "type": "SUBCONTRACT",
                "type_name": "외주가공",
                "order_id": master.id,
                "order_no": master.order_no,
                "order_date": master.order_date,
                "due_date": item.delivery_date or master.delivery_due_date or "",
                "partner_name": master.partner_name,
                "part_no": item.order_part_no,
                "part_name": item.order_part_name,
                "spec": item.spec or "",
                "unit": item.unit,
                "order_qty": order_qty,
                "received_qty": 0.0,
                "remaining_qty": order_qty,
                "stage": "외주출고완료" if active_outbound else "출고대기",
                "note": item.note or master.note or "",
                "previous_part_no": item.previous_part_no,
            })

    rows.sort(key=lambda row: (row.get("due_date") or "9999-12-31", row.get("order_date") or "", row.get("order_no") or ""))
    return {"total": len(rows), "items": rows}
