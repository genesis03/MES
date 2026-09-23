from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import ItemBomModel, ItemMasterModel, ShippingMasterModel
from models.packing import PackingBox, PackingLotAllocation, PackingMaster
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.sales import ShipmentBox, ShipmentItem, ShipmentMaster
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from services.shipping_lot_service import next_shipping_lot_no

router = APIRouter(tags=["Packing"])
templates = Jinja2Templates(directory="templates")


class PackingScanInput(BaseModel):
    part_no: str = Field(min_length=1, max_length=50)
    lot_no: str = Field(min_length=1, max_length=100)
    target_qty: float = Field(gt=0)


class PackingAllocationInput(BaseModel):
    lot_no: str = Field(min_length=1, max_length=100)
    qty: float = Field(gt=0)


class PackingCreateInput(BaseModel):
    packing_date: str = Field(min_length=10, max_length=10)
    part_no: str = Field(min_length=1, max_length=50)
    box_count: int = Field(gt=0)
    box_qty: float = Field(gt=0)
    allocations: list[PackingAllocationInput] = Field(min_length=1)
    note: Optional[str] = Field(default=None, max_length=1000)


def _username(user) -> str:
    return str(getattr(user, "username", None) or getattr(user, "name", None) or "")


def _next_packing_no(db: Session, packing_date: str) -> str:
    """내부 포장 묶음 식별자. 사용자 화면에는 노출하지 않습니다."""
    yymmdd = packing_date.replace("-", "")[2:]
    prefix = f"PK{yymmdd}"
    latest = (
        db.query(PackingMaster.packing_no)
        .filter(PackingMaster.packing_no.like(prefix + "%"))
        .order_by(PackingMaster.packing_no.desc())
        .first()
    )
    seq = 1
    if latest:
        try:
            seq = int(latest[0][-3:]) + 1
        except (TypeError, ValueError):
            seq = 1
    return f"{prefix}{seq:03d}"


def _packed_qty(db: Session, lot_no: str) -> float:
    return float(
        db.query(func.coalesce(func.sum(PackingLotAllocation.allocated_qty), 0.0))
        .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
        .filter(PackingLotAllocation.source_lot_no == lot_no, PackingMaster.status == "PACKED")
        .scalar()
        or 0.0
    )


def _lot_available(db: Session, lot: ProductionLotModel) -> float:
    consumed = float(
        db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0))
        .filter(LotConsumptionModel.lot_no == lot.lot_no).scalar() or 0.0
    )
    related = float(
        db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0))
        .filter(LotRelationModel.parent_lot_no == lot.lot_no).scalar() or 0.0
    )
    reserved_run = float(
        db.query(func.coalesce(func.sum(ProductionRunLotAllocation.allocated_qty), 0.0))
        .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
        .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
        .filter(ProductionRunLotAllocation.lot_no == lot.lot_no, ProductionRun.status == "IN_PROGRESS")
        .scalar() or 0.0
    )
    reserved_subcontract = float(
        db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(
            SubcontractLotAllocation.lot_no == lot.lot_no,
            SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]),
        )
        .scalar() or 0.0
    )
    return max(float(lot.lot_qty or 0) - consumed - related - reserved_run - reserved_subcontract - _packed_qty(db, lot.lot_no), 0.0)


def _packing_source_item_ids(db: Session, finished_item_id: int) -> list[int]:
    """포장 대상 완제품의 직속 하위 생산품 item_id를 반환합니다."""
    bom_rows = (
        db.query(ItemBomModel)
        .filter(ItemBomModel.parent_item_id == finished_item_id)
        .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
        .all()
    )
    if not bom_rows:
        return [finished_item_id]

    final_rows = [row for row in bom_rows if str(row.bom_type or "").upper() == "FINAL"]
    source_rows = final_rows or bom_rows

    result = []
    for row in source_rows:
        child_id = int(row.child_item_id) if row.child_item_id else None
        if not child_id or child_id in result:
            continue
        has_production_lot = (
            db.query(ProductionLotModel.id)
            .filter(
                ProductionLotModel.item_id == child_id,
                ProductionLotModel.status == "ACTIVE",
            )
            .first()
            is not None
        )
        if has_production_lot:
            result.append(child_id)

    if result:
        return result

    return [int(row.child_item_id) for row in source_rows if row.child_item_id]


def _lots(db: Session, finished_item_id: int):
    source_item_ids = _packing_source_item_ids(db, finished_item_id)
    if not source_item_ids:
        return []

    item_map = {
        item.id: item
        for item in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(source_item_ids)).all()
    }
    rows = (
        db.query(ProductionLotModel)
        .filter(
            ProductionLotModel.item_id.in_(source_item_ids),
            ProductionLotModel.status == "ACTIVE",
        )
        .order_by(ProductionLotModel.created_at.asc(), ProductionLotModel.id.asc())
        .all()
    )
    result = []
    for row in rows:
        source_item = item_map.get(row.item_id)
        result.append({
            "lot": row,
            "source_item_id": row.item_id,
            "source_part_no": source_item.part_no if source_item else row.part_no,
            "source_part_name": source_item.part_name if source_item else "",
            "available": _lot_available(db, row),
        })
    return result


def _preview(db: Session, finished_item_id: int, scanned_lot_no: str, target_qty: float):
    rows = _lots(db, finished_item_id)
    scanned_row = next(
        (x for x in rows if x["lot"].lot_no.upper() == scanned_lot_no.upper()),
        None,
    )
    if scanned_row is None:
        raise HTTPException(404, "선택 완제품의 하위 품번에 해당하는 사용 가능한 생산 LOT가 아닙니다.")

    source_item_id = scanned_row["source_item_id"]
    source_part_no = scanned_row["source_part_no"]
    source_rows = [x for x in rows if x["source_item_id"] == source_item_id]
    scan_index = next(
        i for i, x in enumerate(source_rows)
        if x["lot"].lot_no.upper() == scanned_lot_no.upper()
    )

    remaining = float(target_qty)
    allocations = []
    for row in source_rows[: scan_index + 1]:
        if remaining <= 1e-9:
            break
        available = float(row["available"] or 0)
        qty = min(remaining, available)
        if qty > 1e-9:
            allocations.append({
                "lot_no": row["lot"].lot_no,
                "qty": qty,
                "remaining_before": available,
                "source_item_id": row["source_item_id"],
                "source_part_no": row["source_part_no"],
                "source_part_name": row["source_part_name"],
            })
            remaining -= qty
    return allocations, max(remaining, 0.0)


@router.get("/production/packing", response_class=HTMLResponse)
def packing_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="production_packing.html", context={"request": request, "user": current_user})


@router.get("/production/packing/status", response_class=HTMLResponse)
def packing_status_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="production_packing_status.html",
        context={"request": request, "user": current_user},
    )


@router.get("/api/packing/status")
def packing_status(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    part_no: Optional[str] = Query(None, max_length=50),
    lot_no: Optional[str] = Query(None, max_length=60),
    limit: int = Query(2000, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingMaster.status == "PACKED")
    )
    if start_date:
        query = query.filter(PackingMaster.packing_date >= start_date)
    if end_date:
        query = query.filter(PackingMaster.packing_date <= end_date)
    if part_no and part_no.strip():
        value = f"%{part_no.strip()}%"
        query = query.filter(
            (PackingMaster.part_no.ilike(value))
            | (PackingMaster.part_name.ilike(value))
        )
    if lot_no and lot_no.strip():
        query = query.filter(PackingBox.package_lot_no.ilike(f"%{lot_no.strip()}%"))

    rows = (
        query.order_by(
            PackingMaster.packing_date.desc(),
            PackingBox.package_lot_no.desc(),
            PackingBox.id.desc(),
        )
        .limit(limit)
        .all()
    )

    box_ids = [box.id for box, _ in rows]
    shipped_by_box: dict[int, tuple[ShipmentBox, ShipmentMaster]] = {}
    if box_ids:
        shipped_rows = (
            db.query(ShipmentBox, ShipmentMaster)
            .join(ShipmentItem, ShipmentItem.id == ShipmentBox.shipment_item_id)
            .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
            .filter(
                ShipmentBox.packing_box_id.in_(box_ids),
                ShipmentMaster.status == "CONFIRMED",
            )
            .all()
        )
        shipped_by_box = {
            int(shipment_box.packing_box_id): (shipment_box, shipment)
            for shipment_box, shipment in shipped_rows
        }

    return {
        "items": [
            {
                "packing_box_id": box.id,
                "part_no": master.part_no,
                "part_name": master.part_name or "",
                "lot_no": box.package_lot_no,
                "packing_date": master.packing_date,
                "packing_qty": float(box.box_qty or 0),
                "shipment_qty": float(shipped_by_box[box.id][0].shipped_qty or 0)
                if box.id in shipped_by_box else 0.0,
                "shipment_date": shipped_by_box[box.id][1].shipment_date
                if box.id in shipped_by_box else "",
                "customer_name": shipped_by_box[box.id][1].customer_name
                if box.id in shipped_by_box else "",
            }
            for box, master in rows
        ]
    }


@router.get("/api/packing/items")
def packing_items(
    q: Optional[str] = Query(None, max_length=80),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    if q and q.strip():
        query = query.filter(ItemMasterModel.part_no.contains(q.strip(), autoescape=True))
    rows = query.order_by(ItemMasterModel.part_no.asc()).limit(50).all()
    return [{"item_id": x.id, "part_no": x.part_no, "part_name": x.part_name, "moq": int(x.moq or 0), "snp": int(x.snp or 0)} for x in rows]


@router.get("/api/packing/item/{part_no}")
def packing_item(part_no: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no, ItemMasterModel.is_active == "Y").first()
    if not item:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")

    source_item_ids = _packing_source_item_ids(db, item.id)
    source_parts = [
        x.part_no for x in db.query(ItemMasterModel)
        .filter(ItemMasterModel.id.in_(source_item_ids))
        .order_by(ItemMasterModel.part_no.asc())
        .all()
    ]
    lot_rows = []
    for row in _lots(db, item.id):
        lot = row["lot"]
        available = row["available"]
        if available <= 1e-9:
            continue
        lot_rows.append({
            "lot_no": lot.lot_no,
            "source_part_no": row["source_part_no"],
            "source_part_name": row["source_part_name"],
            "remaining_qty": available,
            "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
        })
    standard_qty = int(item.moq or 0) or int(item.snp or 0)
    return {
        "item_id": item.id,
        "part_no": item.part_no,
        "part_name": item.part_name,
        "moq": int(item.moq or 0),
        "snp": int(item.snp or 0),
        "standard_pack_qty": standard_qty,
        "source_parts": source_parts,
        "lots": lot_rows,
    }


@router.post("/api/packing/scan")
def packing_scan(payload: PackingScanInput, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == payload.part_no.strip(), ItemMasterModel.is_active == "Y").first()
    if not item:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")
    allocations, remaining = _preview(db, item.id, payload.lot_no.strip(), payload.target_qty)
    return {
        "allocations": allocations,
        "allocated_qty": sum(x["qty"] for x in allocations),
        "remaining_qty": remaining,
        "complete": remaining <= 1e-9,
    }


@router.post("/api/packing")
def create_packing(payload: PackingCreateInput, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == payload.part_no.strip(), ItemMasterModel.is_active == "Y").first()
    if not item:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")
    target_qty = float(payload.box_count) * float(payload.box_qty)
    supplied_total = sum(float(x.qty) for x in payload.allocations)
    if abs(target_qty - supplied_total) > 1e-6:
        raise HTTPException(409, f"포장수량 {target_qty:g}과 LOT 배정수량 {supplied_total:g}이 일치하지 않습니다.")
    last_lot = payload.allocations[-1].lot_no
    expected, remaining = _preview(db, item.id, last_lot, target_qty)
    if remaining > 1e-9:
        raise HTTPException(409, f"가용 LOT가 부족합니다. 미배정 {remaining:g}")
    expected_map = {x["lot_no"]: round(float(x["qty"]), 9) for x in expected}
    supplied_map = {x.lot_no: round(float(x.qty), 9) for x in payload.allocations}
    if expected_map != supplied_map:
        raise HTTPException(409, "선입선출 배정 결과가 변경되었습니다. LOT를 다시 스캔해 주세요.")

    packing_no = _next_packing_no(db, payload.packing_date)
    master = PackingMaster(
        packing_no=packing_no,
        packing_date=payload.packing_date,
        item_id=item.id,
        part_no=item.part_no,
        part_name=item.part_name,
        box_count=payload.box_count,
        box_qty=payload.box_qty,
        total_qty=target_qty,
        status="PACKED",
        note=(payload.note or "").strip() or None,
        created_by=_username(current_user),
    )
    db.add(master)
    db.flush()

    lot_map = {x["lot"].lot_no: x["lot"] for x in _lots(db, item.id)}
    for row in expected:
        lot = lot_map.get(row["lot_no"])
        master.allocations.append(PackingLotAllocation(
            source_lot_no=row["lot_no"],
            allocated_qty=row["qty"],
            storage_location=lot.storage_location if lot else None,
        ))

    reserved_shipping_lots: set[str] = set()
    for box_no in range(1, payload.box_count + 1):
        package_lot_no = next_shipping_lot_no(db, item.part_no, payload.packing_date, reserved_shipping_lots)
        reserved_shipping_lots.add(package_lot_no)
        master.boxes.append(PackingBox(
            box_no=box_no,
            package_lot_no=package_lot_no,
            box_qty=float(payload.box_qty),
        ))

    db.commit()
    return {
        "id": master.id,
        "packing_no": master.packing_no,
        "waiting_lots": [box.package_lot_no for box in master.boxes],
        "message": "포장 처리가 완료되었습니다.",
    }


def _legacy_shipping_uses_lot(db: Session, package_lot_no: str) -> bool:
    if not package_lot_no:
        return False
    return bool(
        db.query(ShippingMasterModel.id)
        .filter(ShippingMasterModel.row_json.contains(package_lot_no, autoescape=True))
        .first()
    )


def _latest_waiting_box(db: Session, item_id: int) -> PackingBox | None:
    rows = (
        db.query(PackingBox)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .outerjoin(ShipmentBox, ShipmentBox.packing_box_id == PackingBox.id)
        .filter(
            PackingMaster.status == "PACKED",
            PackingMaster.item_id == item_id,
            ShipmentBox.id.is_(None),
        )
        .order_by(
            PackingMaster.packing_date.desc(),
            PackingBox.package_lot_no.desc(),
            PackingBox.id.desc(),
        )
        .all()
    )
    for box in rows:
        if not _legacy_shipping_uses_lot(db, box.package_lot_no):
            return box
    return None


def _release_packing_allocation(db: Session, master: PackingMaster, qty: float) -> None:
    remaining = float(qty or 0)
    for allocation in reversed(list(master.allocations)):
        if remaining <= 1e-9:
            break
        allocated = float(allocation.allocated_qty or 0)
        release_qty = min(allocated, remaining)
        allocation.allocated_qty = allocated - release_qty
        remaining -= release_qty
        if allocation.allocated_qty <= 1e-9:
            db.delete(allocation)
    if remaining > 1e-6:
        raise HTTPException(409, "포장 원 LOT 배정수량이 맞지 않아 취소할 수 없습니다. 관리자 확인이 필요합니다.")


def _cancel_one_box(db: Session, box: PackingBox, current_user) -> dict:
    master = box.master
    if not master or master.status != "PACKED":
        raise HTTPException(409, "취소 가능한 포장 LOT가 아닙니다.")

    if db.query(ShipmentBox.id).filter(ShipmentBox.packing_box_id == box.id).first():
        raise HTTPException(409, f"이미 출고에 사용된 포장 LOT는 취소할 수 없습니다: {box.package_lot_no}")
    if _legacy_shipping_uses_lot(db, box.package_lot_no):
        raise HTTPException(409, f"출고 이력에서 사용된 포장 LOT는 취소할 수 없습니다: {box.package_lot_no}")

    latest = _latest_waiting_box(db, master.item_id)
    if not latest:
        raise HTTPException(409, "취소 가능한 미출고 포장 LOT가 없습니다.")
    if latest.id != box.id:
        raise HTTPException(
            409,
            f"가장 후 포장 LOT {latest.package_lot_no}부터 순서대로 취소해야 합니다.",
        )

    cancel_qty = float(box.box_qty or 0)
    cancelled_lot_no = box.package_lot_no
    _release_packing_allocation(db, master, cancel_qty)

    remaining_box_count = (
        db.query(func.count(PackingBox.id))
        .filter(PackingBox.packing_id == master.id, PackingBox.id != box.id)
        .scalar()
        or 0
    )
    master.box_count = int(remaining_box_count)
    master.total_qty = max(float(master.total_qty or 0) - cancel_qty, 0.0)
    db.delete(box)

    if remaining_box_count == 0:
        master.status = "CANCELLED"
        master.cancelled_by = _username(current_user)
        master.cancelled_at = datetime.now()

    db.commit()
    return {
        "message": f"{cancelled_lot_no} 포장 LOT 1건을 취소했습니다. 원 생산 LOT {cancel_qty:g}이 복원되었습니다.",
        "cancelled_lot_no": cancelled_lot_no,
        "cancelled_qty": cancel_qty,
        "packing_cancelled": remaining_box_count == 0,
    }


@router.get("/api/packing/records")
def packing_records(
    part_no: Optional[str] = Query(None, max_length=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(PackingMaster).filter(PackingMaster.status == "PACKED")
    if part_no and part_no.strip():
        query = query.filter(PackingMaster.part_no == part_no.strip())
    rows = query.order_by(PackingMaster.created_at.desc(), PackingMaster.id.desc()).limit(500).all()

    box_ids = [box.id for master in rows for box in master.boxes]
    shipped_box_ids = set()
    if box_ids:
        shipped_box_ids = {
            int(row[0])
            for row in db.query(ShipmentBox.packing_box_id)
            .filter(ShipmentBox.packing_box_id.in_(box_ids))
            .all()
        }

    latest_by_item: dict[int, int] = {}
    for master in rows:
        if master.item_id not in latest_by_item:
            latest = _latest_waiting_box(db, master.item_id)
            if latest:
                latest_by_item[master.item_id] = latest.id

    result = []
    for master in rows:
        waiting_boxes = [
            box
            for box in master.boxes
            if box.id not in shipped_box_ids and not _legacy_shipping_uses_lot(db, box.package_lot_no)
        ]
        if not waiting_boxes:
            continue
        result.append({
            "id": master.id,
            "packing_no": master.packing_no,
            "packing_date": master.packing_date,
            "item_id": master.item_id,
            "part_no": master.part_no,
            "part_name": master.part_name or "",
            "total_qty": sum(float(box.box_qty or 0) for box in waiting_boxes),
            "box_count": len(waiting_boxes),
            "box_qty": float(master.box_qty or 0),
            "waiting_lots": [box.package_lot_no for box in waiting_boxes],
            "waiting_boxes": [
                {
                    "id": box.id,
                    "package_lot_no": box.package_lot_no,
                    "box_qty": float(box.box_qty or 0),
                    "can_cancel": latest_by_item.get(master.item_id) == box.id,
                }
                for box in waiting_boxes
            ],
        })
    return result


@router.post("/api/packing/boxes/{box_id}/cancel")
def cancel_packing_box(box_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    box = db.get(PackingBox, box_id)
    if not box:
        raise HTTPException(404, "포장 LOT를 찾을 수 없습니다.")
    return _cancel_one_box(db, box, current_user)


@router.post("/api/packing/{packing_id}/cancel")
def cancel_packing(packing_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """기존 화면/캐시 호환용. 전체취소하지 않고 해당 포장묶음의 가장 후 미출고 LOT 1건만 취소합니다."""
    master = db.get(PackingMaster, packing_id)
    if not master:
        raise HTTPException(404, "포장 내역을 찾을 수 없습니다.")
    if master.status == "CANCELLED":
        return {"message": "이미 취소된 포장입니다."}

    candidates = [
        box for box in master.boxes
        if not db.query(ShipmentBox.id).filter(ShipmentBox.packing_box_id == box.id).first()
        and not _legacy_shipping_uses_lot(db, box.package_lot_no)
    ]
    if not candidates:
        raise HTTPException(409, "이 포장묶음에는 취소 가능한 미출고 포장 LOT가 없습니다.")
    box = sorted(candidates, key=lambda x: (x.package_lot_no, x.id), reverse=True)[0]
    return _cancel_one_box(db, box, current_user)
