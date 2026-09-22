from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import ItemMasterModel, PurchaseInboundItem, PurchaseInboundMaster, StorageLocationModel
from models.packing import PackingLotAllocation, PackingMaster
from models.production_lot import ProductionLotModel
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundItem, SubcontractOutboundLot, SubcontractOutboundMaster

router = APIRouter(tags=["Inventory LOT Location"])


def _used_qty(db: Session, lot_no: str) -> float:
    consumed = db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0)).filter(LotConsumptionModel.lot_no == lot_no).scalar() or 0.0
    related = db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0)).filter(LotRelationModel.parent_lot_no == lot_no).scalar() or 0.0
    packed = (
        db.query(func.coalesce(func.sum(PackingLotAllocation.allocated_qty), 0.0))
        .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
        .filter(PackingLotAllocation.source_lot_no == lot_no, PackingMaster.status == "PACKED")
        .scalar() or 0.0
    )
    subcontract_reserved = (
        db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(SubcontractLotAllocation.lot_no == lot_no, SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]))
        .scalar() or 0.0
    )
    return float(consumed) + float(related) + float(packed) + float(subcontract_reserved)


def _storage_map(db: Session) -> dict[str, str]:
    return {row.location_code: row.location_name for row in db.query(StorageLocationModel).all() if row.location_code}


def _storage_display(code: Optional[str], names: dict[str, str]) -> str:
    value = str(code or "").strip()
    return names.get(value, value) if value else ""


def _current_storage(db: Session, lot_no: str, original: Optional[str]) -> str:
    # 전량 외주입고로 원 LOT를 그대로 유지한 경우 입고 저장위치가 최신 위치입니다.
    inbound = (
        db.query(SubcontractInboundMaster)
        .join(SubcontractInboundItem, SubcontractInboundItem.inbound_id == SubcontractInboundMaster.id)
        .join(SubcontractInboundLot, SubcontractInboundLot.inbound_item_id == SubcontractInboundItem.id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.source_lot_no == lot_no,
            SubcontractInboundLot.child_lot_no == lot_no,
        )
        .order_by(SubcontractInboundMaster.created_at.desc(), SubcontractInboundMaster.id.desc())
        .first()
    )
    if inbound:
        return inbound.storage_location or original or ""

    # 외주 출고 완료 중인 LOT는 발주 시 지정한 공정 저장위치를 현재 위치로 봅니다.
    outbound = (
        db.query(SubcontractOutboundMaster)
        .join(SubcontractOutboundItem, SubcontractOutboundItem.outbound_id == SubcontractOutboundMaster.id)
        .join(SubcontractOutboundLot, SubcontractOutboundLot.outbound_item_id == SubcontractOutboundItem.id)
        .filter(SubcontractOutboundMaster.status == "OUTBOUND", SubcontractOutboundLot.lot_no == lot_no)
        .order_by(SubcontractOutboundMaster.created_at.desc(), SubcontractOutboundMaster.id.desc())
        .first()
    )
    if outbound:
        return outbound.external_storage_location or original or ""
    return original or ""


@router.get("/api/inventory/lots")
def inventory_lots_with_current_location(
    part_no: Optional[list[str]] = Query(None),
    stock_status: Optional[str] = Query("ALL"),
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

    names = _storage_map(db)
    selected_items = db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(selected)).all()
    item_map = {item.id: item for item in selected_items}
    item_ids = [item.id for item in selected_items]
    rows = []

    purchase_rows = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.item_id.in_(item_ids),
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .order_by(PurchaseInboundMaster.created_at.desc(), PurchaseInboundItem.id.desc())
        .all()
    )
    for item, master in purchase_rows:
        qty = float(item.inbound_qty or 0)
        used = _used_qty(db, item.internal_lot_no)
        part = item_map.get(item.item_id)
        rows.append({
            "source": "구매입고", "item_id": item.item_id, "part_no": part.part_no if part else item.part_no, "part_name": part.part_name if part else "",
            "lot_no": item.internal_lot_no,
            "created_at": master.created_at.strftime("%Y-%m-%d %H:%M:%S") if master.created_at else master.inbound_date,
            "lot_qty": qty, "used_qty": used, "remaining_qty": max(qty - used, 0.0),
            "storage_location": _storage_display(_current_storage(db, item.internal_lot_no, item.storage_location), names),
        })

    production_rows = db.query(ProductionLotModel).filter(ProductionLotModel.item_id.in_(item_ids)).order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc()).all()
    for lot in production_rows:
        qty = float(lot.lot_qty or 0)
        used = _used_qty(db, lot.lot_no)
        part = item_map.get(lot.item_id)
        rows.append({
            "source": "생산", "item_id": lot.item_id, "part_no": part.part_no if part else lot.part_no, "part_name": part.part_name if part else "",
            "lot_no": lot.lot_no,
            "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
            "lot_qty": qty, "used_qty": used, "remaining_qty": max(qty - used, 0.0),
            "storage_location": _storage_display(_current_storage(db, lot.lot_no, lot.storage_location), names),
        })

    status = str(stock_status or "ALL").strip().upper()
    if status not in {"ALL", "REMAINING", "USED"}:
        status = "ALL"
    if status == "REMAINING":
        rows = [row for row in rows if float(row["remaining_qty"] or 0) > 1e-9]
    elif status == "USED":
        rows = [row for row in rows if float(row["remaining_qty"] or 0) <= 1e-9]

    rows.sort(key=lambda row: (row["part_no"], row["created_at"], row["lot_no"]), reverse=True)
    return {
        "items": rows,
        "total": len(rows),
        "selected_parts": selected,
        "stock_status": status,
    }
