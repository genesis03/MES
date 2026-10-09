from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.inventory_adjustment import InventoryAdjustmentModel
from models.inventory_movement import InventoryMovementModel
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import ItemMasterModel, PurchaseInboundItem, PurchaseInboundMaster, StorageLocationModel
from models.packing import PackingLotAllocation, PackingMaster
from models.production_lot import ProductionLotModel
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundItem, SubcontractOutboundLot, SubcontractOutboundMaster
from services.packing_inventory_service import packing_stock_snapshot
from services.production_defect_service import active_production_defect_qty

router = APIRouter(tags=["Inventory LOT Location"])


class InventoryAdjustmentInput(BaseModel):
    item_id: int
    lot_no: str = Field(min_length=1, max_length=100)
    after_qty: float = Field(ge=0)
    reason: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(None, max_length=1000)


def _used_qty(db: Session, lot_no: str, item_id: int | None = None) -> float:
    consumed = db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0)).filter(LotConsumptionModel.lot_no == lot_no).scalar() or 0.0
    related = db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0)).filter(LotRelationModel.parent_lot_no == lot_no).scalar() or 0.0
    packed = (
        db.query(func.coalesce(func.sum(PackingLotAllocation.allocated_qty), 0.0))
        .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
        .filter(PackingLotAllocation.source_lot_no == lot_no, PackingMaster.status == "PACKED")
        .scalar() or 0.0
    )
    subcontract_query = (
        db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(SubcontractLotAllocation.lot_no == lot_no, SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]))
    )
    if item_id:
        subcontract_query = subcontract_query.filter(SubcontractOrderItem.previous_item_id == item_id)
    subcontract_reserved = subcontract_query.scalar() or 0.0
    sample_used = (
        db.query(func.coalesce(func.sum(SubcontractInboundLot.sample_qty), 0.0))
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.child_lot_no == lot_no,
        )
        .scalar()
        or 0.0
    )
    defected = active_production_defect_qty(db, lot_no, item_id)
    return float(consumed) + float(related) + float(packed) + float(subcontract_reserved) + float(sample_used) + float(defected)


def _adjustment_qty(db: Session, lot_no: str, item_id: int | None = None) -> float:
    query = db.query(func.coalesce(func.sum(InventoryAdjustmentModel.adjustment_qty), 0.0)).filter(
        InventoryAdjustmentModel.lot_no == lot_no
    )
    if item_id:
        query = query.filter(InventoryAdjustmentModel.item_id == item_id)
    return float(query.scalar() or 0.0)


def _storage_map(db: Session) -> dict[str, str]:
    return {row.location_code: row.location_name for row in db.query(StorageLocationModel).all() if row.location_code}


def _storage_display(code: Optional[str], names: dict[str, str]) -> str:
    value = str(code or "").strip()
    return names.get(value, value) if value else ""


def _current_storage(db: Session, lot_no: str, original: Optional[str], item_id: int) -> str:
    # 수동 창고/저장위치 이동 이력이 있으면 가장 최신 위치를 최우선으로 봅니다.
    movement = (
        db.query(InventoryMovementModel)
        .filter(InventoryMovementModel.lot_no == lot_no, InventoryMovementModel.item_id == item_id)
        .order_by(InventoryMovementModel.created_at.desc(), InventoryMovementModel.id.desc())
        .first()
    )
    if movement:
        return movement.to_location or original or ""

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
        used = _used_qty(db, item.internal_lot_no, item.item_id)
        part = item_map.get(item.item_id)
        rows.append({
            "source": "구매입고", "item_id": item.item_id, "part_no": part.part_no if part else item.part_no, "part_name": part.part_name if part else "",
            "lot_no": item.internal_lot_no,
            "created_at": master.created_at.strftime("%Y-%m-%d %H:%M:%S") if master.created_at else master.inbound_date,
            "lot_qty": qty, "used_qty": used, "adjustment_qty": _adjustment_qty(db, item.internal_lot_no, item.item_id), "remaining_qty": max(qty - used + _adjustment_qty(db, item.internal_lot_no, item.item_id), 0.0),
            "storage_location": _storage_display(_current_storage(db, item.internal_lot_no, item.storage_location, item.item_id), names),
        })

    production_rows = (
        db.query(ProductionLotModel)
        .filter(
            ProductionLotModel.item_id.in_(item_ids),
            ProductionLotModel.status == "ACTIVE",
        )
        .order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc())
        .all()
    )
    for lot in production_rows:
        qty = float(lot.lot_qty or 0)
        used = _used_qty(db, lot.lot_no, lot.item_id)
        part = item_map.get(lot.item_id)
        rows.append({
            "source": "생산", "item_id": lot.item_id, "part_no": part.part_no if part else lot.part_no, "part_name": part.part_name if part else "",
            "lot_no": lot.lot_no,
            "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
            "lot_qty": qty, "used_qty": used, "adjustment_qty": _adjustment_qty(db, lot.lot_no, lot.item_id), "remaining_qty": max(qty - used + _adjustment_qty(db, lot.lot_no, lot.item_id), 0.0),
            "storage_location": _storage_display(_current_storage(db, lot.lot_no, lot.storage_location, lot.item_id), names),
        })

    packed_rows, _ = packing_stock_snapshot(db)
    for row in packed_rows:
        if row['item_id'] not in item_map:
            continue
        row['storage_location'] = _storage_display(row['storage_location'], names)
        rows.append(row)

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


@router.get("/api/inventory/status")
def inventory_status(
    q: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = str(q or "").strip().lower()
    names = _storage_map(db)
    grouped: dict[tuple[int, str], dict] = {}

    item_query = db.query(ItemMasterModel)
    if keyword:
        item_query = item_query.filter(
            (ItemMasterModel.part_no.ilike(f"%{keyword}%"))
            | (ItemMasterModel.part_name.ilike(f"%{keyword}%"))
        )
    items = item_query.all()
    item_map = {row.id: row for row in items}
    item_ids = list(item_map)
    if not item_ids:
        return {"items": [], "total": 0, "stock_qty": 0.0}

    def add_stock(item_id: int, lot_no: str, lot_qty: float, original_location: Optional[str]):
        remaining = max(float(lot_qty or 0) - _used_qty(db, lot_no, item_id) + _adjustment_qty(db, lot_no, item_id), 0.0)
        if remaining <= 1e-9:
            return
        location_code = _current_storage(db, lot_no, original_location, item_id)
        key = (int(item_id), str(location_code or ""))
        item = item_map.get(item_id)
        if item is None:
            return
        row = grouped.setdefault(
            key,
            {
                "item_id": item.id,
                "part_no": item.part_no,
                "part_name": item.part_name or "",
                "spec": item.spec or "",
                "unit": item.unit or "EA",
                "storage_location": _storage_display(location_code, names),
                "lot_count": 0,
                "stock_qty": 0.0,
            },
        )
        row["lot_count"] += 1
        row["stock_qty"] += remaining

    purchase_rows = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.item_id.in_(item_ids),
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .all()
    )
    for item, _master in purchase_rows:
        add_stock(item.item_id, item.internal_lot_no, item.inbound_qty, item.storage_location)

    production_rows = (
        db.query(ProductionLotModel)
        .filter(
            ProductionLotModel.item_id.in_(item_ids),
            ProductionLotModel.status == "ACTIVE",
        )
        .all()
    )
    for lot in production_rows:
        add_stock(lot.item_id, lot.lot_no, lot.lot_qty, lot.storage_location)

    packed_rows, _ = packing_stock_snapshot(db)
    for packed in packed_rows:
        item = item_map.get(packed['item_id'])
        if item is None or packed['remaining_qty'] <= 1e-9:
            continue
        key = (item.id, str(packed['storage_location'] or ''))
        row = grouped.setdefault(key, {
            'item_id': item.id, 'part_no': item.part_no, 'part_name': item.part_name or '',
            'spec': item.spec or '', 'unit': item.unit or 'EA',
            'storage_location': _storage_display(packed['storage_location'], names),
            'lot_count': 0, 'stock_qty': 0.0,
        })
        row['lot_count'] += 1
        row['stock_qty'] += packed['remaining_qty']

    rows = list(grouped.values())
    rows.sort(key=lambda row: (row["part_no"], row["storage_location"]))
    total_qty = sum(float(row["stock_qty"] or 0) for row in rows)
    return {"items": rows, "total": len(rows), "stock_qty": total_qty}


def _lot_base_info(db: Session, item_id: int, lot_no: str):
    purchase = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.item_id == item_id,
            PurchaseInboundItem.internal_lot_no == lot_no,
        )
        .first()
    )
    if purchase:
        item, _master = purchase
        return float(item.inbound_qty or 0), item.storage_location, "구매입고"

    lot = (
        db.query(ProductionLotModel)
        .filter(
            ProductionLotModel.item_id == item_id,
            ProductionLotModel.lot_no == lot_no,
            ProductionLotModel.status == "ACTIVE",
        )
        .one_or_none()
    )
    if lot:
        return float(lot.lot_qty or 0), lot.storage_location, "생산"
    return None


@router.get("/api/inventory/adjustments/lots")
def inventory_adjustment_lots(
    keyword: Optional[str] = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    q = str(keyword or "").strip()
    names = _storage_map(db)
    item_query = db.query(ItemMasterModel)
    if q:
        item_query = item_query.filter(
            or_(
                ItemMasterModel.part_no.ilike(f"%{q}%"),
                ItemMasterModel.part_name.ilike(f"%{q}%"),
                ItemMasterModel.id.in_(db.query(PurchaseInboundItem.item_id).filter(
                    PurchaseInboundItem.internal_lot_no.ilike(f"%{q}%"))),
                ItemMasterModel.id.in_(db.query(ProductionLotModel.item_id).filter(
                    ProductionLotModel.lot_no.ilike(f"%{q}%"))),
            )
        )
    items = item_query.limit(limit).all()
    item_map = {row.id: row for row in items}
    item_ids = list(item_map)

    rows = []
    if item_ids:
        purchase_rows = (
            db.query(PurchaseInboundItem, PurchaseInboundMaster)
            .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
            .filter(
                PurchaseInboundMaster.status == "CONFIRMED",
                PurchaseInboundItem.item_id.in_(item_ids),
                PurchaseInboundItem.internal_lot_no.isnot(None),
                PurchaseInboundItem.internal_lot_no != "",
            )
            .all()
        )
        for item, master in purchase_rows:
            if q and q.lower() not in str(item.internal_lot_no or "").lower():
                part = item_map.get(item.item_id)
                if part and q.lower() not in part.part_no.lower() and q.lower() not in (part.part_name or "").lower():
                    continue
            base_qty = float(item.inbound_qty or 0)
            used_qty = _used_qty(db, item.internal_lot_no, item.item_id)
            adjustment_qty = _adjustment_qty(db, item.internal_lot_no, item.item_id)
            current_qty = max(base_qty - used_qty + adjustment_qty, 0.0)
            part = item_map.get(item.item_id)
            if current_qty <= 1e-9:
                continue
            rows.append({
                "source": "구매입고",
                "item_id": item.item_id,
                "part_no": part.part_no if part else item.part_no,
                "part_name": part.part_name if part else "",
                "lot_no": item.internal_lot_no,
                "storage_location": _storage_display(_current_storage(db, item.internal_lot_no, item.storage_location, item.item_id), names),
                "current_qty": current_qty,
                "unit": part.unit if part else item.unit,
                "created_at": master.inbound_date,
            })

        production_rows = (
            db.query(ProductionLotModel)
            .filter(ProductionLotModel.item_id.in_(item_ids), ProductionLotModel.status == "ACTIVE")
            .all()
        )
        for lot in production_rows:
            if q and q.lower() not in str(lot.lot_no or "").lower():
                part = item_map.get(lot.item_id)
                if part and q.lower() not in part.part_no.lower() and q.lower() not in (part.part_name or "").lower():
                    continue
            base_qty = float(lot.lot_qty or 0)
            used_qty = _used_qty(db, lot.lot_no, lot.item_id)
            adjustment_qty = _adjustment_qty(db, lot.lot_no, lot.item_id)
            current_qty = max(base_qty - used_qty + adjustment_qty, 0.0)
            part = item_map.get(lot.item_id)
            if current_qty <= 1e-9:
                continue
            rows.append({
                "source": "생산",
                "item_id": lot.item_id,
                "part_no": part.part_no if part else lot.part_no,
                "part_name": part.part_name if part else "",
                "lot_no": lot.lot_no,
                "storage_location": _storage_display(_current_storage(db, lot.lot_no, lot.storage_location, lot.item_id), names),
                "current_qty": current_qty,
                "unit": part.unit if part else "EA",
                "created_at": lot.created_at.strftime("%Y-%m-%d") if lot.created_at else "",
            })

    rows.sort(key=lambda row: (row["part_no"], row["lot_no"]))
    return {"items": rows[:limit], "total": min(len(rows), limit)}


@router.post("/api/inventory/adjustments")
def create_inventory_adjustment(
    payload: InventoryAdjustmentInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    lot_no = payload.lot_no.strip()
    item = db.get(ItemMasterModel, payload.item_id)
    if item is None:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")

    base = _lot_base_info(db, payload.item_id, lot_no)
    if base is None:
        raise HTTPException(404, "조정할 LOT를 찾을 수 없습니다.")
    base_qty, original_location, _source = base
    current_qty = max(
        base_qty - _used_qty(db, lot_no, payload.item_id) + _adjustment_qty(db, lot_no, payload.item_id),
        0.0,
    )
    after_qty = float(payload.after_qty)
    adjustment_qty = after_qty - current_qty
    if abs(adjustment_qty) <= 1e-9:
        raise HTTPException(422, "현재 재고와 조정 후 재고가 같습니다.")

    location_code = _current_storage(db, lot_no, original_location, payload.item_id)
    row = InventoryAdjustmentModel(
        item_id=item.id,
        part_no=item.part_no,
        lot_no=lot_no,
        storage_location=location_code or None,
        before_qty=current_qty,
        adjustment_qty=adjustment_qty,
        after_qty=after_qty,
        reason=payload.reason.strip(),
        note=(payload.note or "").strip() or None,
        created_by=getattr(current_user, "username", None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "item_id": row.item_id,
        "part_no": row.part_no,
        "lot_no": row.lot_no,
        "before_qty": row.before_qty,
        "adjustment_qty": row.adjustment_qty,
        "after_qty": row.after_qty,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
    }


@router.get("/api/inventory/adjustments/history")
def inventory_adjustment_history(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = (
        db.query(InventoryAdjustmentModel)
        .order_by(InventoryAdjustmentModel.created_at.desc(), InventoryAdjustmentModel.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "part_no": row.part_no,
                "lot_no": row.lot_no,
                "storage_location": _storage_display(row.storage_location, _storage_map(db)),
                "before_qty": row.before_qty,
                "adjustment_qty": row.adjustment_qty,
                "after_qty": row.after_qty,
                "reason": row.reason,
                "note": row.note or "",
                "created_by": row.created_by or "",
                "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            }
            for row in rows
        ]
    }


class InventoryMovementInput(BaseModel):
    item_id: int
    lot_no: str = Field(min_length=1, max_length=100)
    to_location: str = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=100)
    note: Optional[str] = Field(None, max_length=1000)


@router.get("/api/inventory/movements/locations")
def inventory_movement_locations(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = (
        db.query(StorageLocationModel)
        .filter(StorageLocationModel.is_active == "Y")
        .order_by(StorageLocationModel.sort_order.asc(), StorageLocationModel.location_name.asc())
        .all()
    )
    return {
        "items": [
            {"code": row.location_code, "name": row.location_name}
            for row in rows
        ]
    }


@router.get("/api/inventory/movements/lots")
def inventory_movement_lots(
    keyword: Optional[str] = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    data = inventory_adjustment_lots(keyword=keyword, limit=limit, db=db, current_user=current_user)
    # Adjustments remain restricted to native input/production stock. Packing
    # balances can move locations without changing the source quantities.
    names = _storage_map(db)
    rows = list(data['items'])
    packed, _ = packing_stock_snapshot(db)
    search = str(keyword or '').strip().casefold()
    for row in packed:
        if row['remaining_qty'] <= 1e-9:
            continue
        if search and not any(search in str(row[key]).casefold() for key in ('part_no', 'part_name', 'lot_no')):
            continue
        item = db.get(ItemMasterModel, row['item_id'])
        rows.append({**row, 'current_qty': row['remaining_qty'], 'unit': item.unit or 'EA',
                     'storage_location': _storage_display(row['storage_location'], names)})
    rows.sort(key=lambda row: (row['part_no'], row['lot_no']))
    return {'items': rows[:limit], 'total': len(rows)}


@router.post("/api/inventory/movements")
def create_inventory_movement(
    payload: InventoryMovementInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    lot_no = payload.lot_no.strip()
    item = db.get(ItemMasterModel, payload.item_id)
    if item is None:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")

    base = _lot_base_info(db, payload.item_id, lot_no)
    if base is None:
        packed, _ = packing_stock_snapshot(db)
        matches = [row for row in packed if row['item_id'] == item.id
                   and row['lot_no'].strip().casefold() == lot_no.casefold()]
        if len(matches) != 1:
            raise HTTPException(404, "이동할 LOT를 찾을 수 없습니다.")
        stock = matches[0]
        lot_no = stock['lot_no']
        current_qty = stock['remaining_qty']
        original_location = stock['storage_location']
    else:
        base_qty, original_location, _source = base
        current_qty = max(
            base_qty - _used_qty(db, lot_no, payload.item_id) + _adjustment_qty(db, lot_no, payload.item_id),
            0.0,
        )
    if current_qty <= 1e-9:
        raise HTTPException(409, "현재 재고가 0인 LOT는 이동할 수 없습니다.")

    from_location = _current_storage(db, lot_no, original_location, payload.item_id)
    to_location = payload.to_location.strip()
    if from_location == to_location:
        raise HTTPException(422, "현재 저장위치와 이동할 저장위치가 같습니다.")

    target = (
        db.query(StorageLocationModel)
        .filter(
            StorageLocationModel.location_code == to_location,
            StorageLocationModel.is_active == "Y",
        )
        .one_or_none()
    )
    if target is None:
        raise HTTPException(404, "이동할 저장위치를 찾을 수 없습니다.")

    row = InventoryMovementModel(
        item_id=item.id,
        part_no=item.part_no,
        lot_no=lot_no,
        from_location=from_location or None,
        to_location=to_location,
        moved_qty=current_qty,
        reason=payload.reason.strip(),
        note=(payload.note or "").strip() or None,
        created_by=getattr(current_user, "username", None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "part_no": row.part_no,
        "lot_no": row.lot_no,
        "from_location": _storage_display(row.from_location, _storage_map(db)),
        "to_location": _storage_display(row.to_location, _storage_map(db)),
        "moved_qty": row.moved_qty,
        "reason": row.reason,
        "created_by": row.created_by,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
    }


@router.get("/api/inventory/movements/history")
def inventory_movement_history(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    names = _storage_map(db)
    rows = (
        db.query(InventoryMovementModel)
        .order_by(InventoryMovementModel.created_at.desc(), InventoryMovementModel.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "part_no": row.part_no,
                "lot_no": row.lot_no,
                "from_location": _storage_display(row.from_location, names),
                "to_location": _storage_display(row.to_location, names),
                "moved_qty": row.moved_qty,
                "reason": row.reason,
                "note": row.note or "",
                "created_by": row.created_by or "",
                "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            }
            for row in rows
        ]
    }
