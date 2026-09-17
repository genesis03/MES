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
from models.models import (
    ItemMasterModel,
    PurchaseInboundItem,
    PurchaseInboundMaster,
    StorageLocationModel,
)
from models.packing import PackingLotAllocation, PackingMaster
from models.production_lot import ProductionLotModel
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster

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
    packed = (
        db.query(func.coalesce(func.sum(PackingLotAllocation.allocated_qty), 0.0))
        .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
        .filter(
            PackingLotAllocation.source_lot_no == lot_no,
            PackingMaster.status == "PACKED",
        )
        .scalar()
        or 0.0
    )
    subcontract_reserved = (
        db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(
            SubcontractLotAllocation.lot_no == lot_no,
            SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]),
        )
        .scalar()
        or 0.0
    )
    return float(consumed) + float(related) + float(packed) + float(subcontract_reserved)


def _storage_name_map(db: Session) -> dict[str, str]:
    return {
        row.location_code: row.location_name
        for row in db.query(StorageLocationModel).all()
        if row.location_code
    }


def _storage_display(value: Optional[str], storage_map: dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return storage_map.get(raw, raw)


@router.get("/api/inventory/items")
def inventory_item_candidates(
    q: Optional[str] = Query(None, max_length=80),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = (q or "").strip()
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    if keyword:
        query = query.filter(ItemMasterModel.part_no.contains(keyword, autoescape=True))
    rows = query.order_by(ItemMasterModel.part_no.asc()).limit(limit).all()
    return [
        {"part_no": row.part_no, "part_name": row.part_name or ""}
        for row in rows
    ]


@router.get("/api/inventory/lots")
def inventory_lots(
    part_no: Optional[list[str]] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    selected = []
    for value in part_no or []:
        text = str(value or "").strip()
        if text and text not in selected:
            selected.append(text)
    if not selected:
        return {"items": [], "total": 0, "selected_parts": []}

    rows = []
    storage_map = _storage_name_map(db)
    item_map = {
        item.part_no: item
        for item in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(selected)).all()
    }

    purchase_query = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.part_no.in_(selected),
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
    )
    for item, master in purchase_query.order_by(PurchaseInboundMaster.created_at.desc(), PurchaseInboundItem.id.desc()).all():
        lot_qty = float(item.inbound_qty or 0)
        used_qty = _used_qty(db, item.internal_lot_no)
        part = item_map.get(item.part_no)
        rows.append({
            "source": "구매입고",
            "part_no": item.part_no,
            "part_name": part.part_name if part else "",
            "lot_no": item.internal_lot_no,
            "created_at": master.created_at.strftime("%Y-%m-%d %H:%M:%S") if master.created_at else master.inbound_date,
            "lot_qty": lot_qty,
            "used_qty": used_qty,
            "remaining_qty": max(lot_qty - used_qty, 0.0),
            "storage_location": _storage_display(item.storage_location, storage_map),
        })

    production_query = db.query(ProductionLotModel).filter(ProductionLotModel.part_no.in_(selected))
    for lot in production_query.order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc()).all():
        lot_qty = float(lot.lot_qty or 0)
        used_qty = _used_qty(db, lot.lot_no)
        part = item_map.get(lot.part_no)
        rows.append({
            "source": "생산",
            "part_no": lot.part_no,
            "part_name": part.part_name if part else "",
            "lot_no": lot.lot_no,
            "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
            "lot_qty": lot_qty,
            "used_qty": used_qty,
            "remaining_qty": max(lot_qty - used_qty, 0.0),
            "storage_location": _storage_display(lot.storage_location, storage_map),
        })

    rows.sort(key=lambda x: (x["part_no"], x["created_at"], x["lot_no"]), reverse=True)
    return {"items": rows, "total": len(rows), "selected_parts": selected}
