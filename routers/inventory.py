from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import ItemMasterModel, PurchaseInboundItem, PurchaseInboundMaster
from models.production_lot import ProductionLotModel

router = APIRouter(tags=["Inventory"])
templates = Jinja2Templates(directory="templates")


@router.get("/inventory/lots", response_class=HTMLResponse)
def inventory_lots_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="inventory_lots.html",
        context={"request": request, "user": current_user},
    )


def _used_qty(db: Session, lot_no: str) -> float:
    consumed = (
        db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0))
        .filter(LotConsumptionModel.lot_no == lot_no)
        .scalar()
        or 0.0
    )
    related = (
        db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0))
        .filter(LotRelationModel.parent_lot_no == lot_no)
        .scalar()
        or 0.0
    )
    return float(consumed) + float(related)


@router.get("/api/inventory/lots")
def inventory_lots(
    part_no: Optional[str] = Query(None, max_length=80),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = (part_no or "").strip()
    if not keyword:
        return {"items": [], "total": 0}

    rows = []

    purchase_query = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.part_no.contains(keyword, autoescape=True),
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
    )
    for item, master in purchase_query.order_by(PurchaseInboundMaster.created_at.desc(), PurchaseInboundItem.id.desc()).all():
        lot_qty = float(item.inbound_qty or 0)
        used_qty = _used_qty(db, item.internal_lot_no)
        part = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == item.part_no).first()
        rows.append({
            "source": "구매입고",
            "part_no": item.part_no,
            "part_name": part.part_name if part else "",
            "lot_no": item.internal_lot_no,
            "created_at": master.created_at.strftime("%Y-%m-%d %H:%M:%S") if master.created_at else master.inbound_date,
            "lot_qty": lot_qty,
            "used_qty": used_qty,
            "remaining_qty": max(lot_qty - used_qty, 0.0),
            "storage_location": item.storage_location or "",
        })

    production_query = db.query(ProductionLotModel).filter(
        ProductionLotModel.part_no.contains(keyword, autoescape=True)
    )
    for lot in production_query.order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc()).all():
        lot_qty = float(lot.lot_qty or 0)
        used_qty = _used_qty(db, lot.lot_no)
        part = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == lot.part_no).first()
        rows.append({
            "source": "생산",
            "part_no": lot.part_no,
            "part_name": part.part_name if part else "",
            "lot_no": lot.lot_no,
            "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
            "lot_qty": lot_qty,
            "used_qty": used_qty,
            "remaining_qty": max(lot_qty - used_qty, 0.0),
            "storage_location": lot.storage_location or "",
        })

    rows.sort(key=lambda x: (x["part_no"], x["created_at"], x["lot_no"]), reverse=True)
    return {"items": rows, "total": len(rows)}
