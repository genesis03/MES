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
from models.models import ItemMasterModel, ShippingMasterModel
from models.packing import PackingBox, PackingLotAllocation, PackingMaster
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster

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


def _lots(db: Session, part_no: str):
    rows = db.query(ProductionLotModel).filter(
        ProductionLotModel.part_no == part_no,
        ProductionLotModel.status == "ACTIVE",
    ).order_by(ProductionLotModel.created_at.asc(), ProductionLotModel.id.asc()).all()
    return [(row, _lot_available(db, row)) for row in rows]


def _preview(db: Session, part_no: str, scanned_lot_no: str, target_qty: float):
    rows = _lots(db, part_no)
    scan_index = next((i for i, (row, _) in enumerate(rows) if row.lot_no.upper() == scanned_lot_no.upper()), None)
    if scan_index is None:
        raise HTTPException(404, "선택 품번의 사용 가능한 생산 LOT가 아닙니다.")
    remaining = float(target_qty)
    allocations = []
    for row, available in rows[: scan_index + 1]:
        if remaining <= 1e-9:
            break
        qty = min(remaining, available)
        if qty > 1e-9:
            allocations.append({"lot_no": row.lot_no, "qty": qty, "remaining_before": available})
            remaining -= qty
    return allocations, max(remaining, 0.0)


@router.get("/production/packing", response_class=HTMLResponse)
def packing_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="production_packing.html", context={"request": request, "user": current_user})


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
    return [{"part_no": x.part_no, "part_name": x.part_name, "moq": int(x.moq or 0), "snp": int(x.snp or 0)} for x in rows]


@router.get("/api/packing/item/{part_no}")
def packing_item(part_no: str, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no, ItemMasterModel.is_active == "Y").first()
    if not item:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")
    lot_rows = []
    for lot, available in _lots(db, item.part_no):
        if available <= 1e-9:
            continue
        lot_rows.append({
            "lot_no": lot.lot_no,
            "remaining_qty": available,
            "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
        })
    standard_qty = int(item.moq or 0) or int(item.snp or 0)
    return {
        "part_no": item.part_no,
        "part_name": item.part_name,
        "moq": int(item.moq or 0),
        "snp": int(item.snp or 0),
        "standard_pack_qty": standard_qty,
        "lots": lot_rows,
    }


@router.post("/api/packing/scan")
def packing_scan(payload: PackingScanInput, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    allocations, remaining = _preview(db, payload.part_no.strip(), payload.lot_no.strip(), payload.target_qty)
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
    expected, remaining = _preview(db, item.part_no, last_lot, target_qty)
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
    lot_map = {lot.lot_no: lot for lot, _ in _lots(db, item.part_no)}
    for row in expected:
        lot = lot_map.get(row["lot_no"])
        master.allocations.append(PackingLotAllocation(
            source_lot_no=row["lot_no"],
            allocated_qty=row["qty"],
            storage_location=lot.storage_location if lot else None,
        ))
    for box_no in range(1, payload.box_count + 1):
        master.boxes.append(PackingBox(
            box_no=box_no,
            package_lot_no=f"{packing_no}-{box_no:02d}",
            box_qty=float(payload.box_qty),
        ))
    db.commit()
    return {"id": master.id, "packing_no": master.packing_no, "message": "포장 처리가 완료되었습니다."}


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
    return [{
        "id": x.id,
        "packing_no": x.packing_no,
        "packing_date": x.packing_date,
        "part_no": x.part_no,
        "part_name": x.part_name or "",
        "total_qty": float(x.total_qty or 0),
        "box_count": x.box_count,
        "box_qty": float(x.box_qty or 0),
        "package_lots": [b.package_lot_no for b in x.boxes],
    } for x in rows]


@router.post("/api/packing/{packing_id}/cancel")
def cancel_packing(packing_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    master = db.get(PackingMaster, packing_id)
    if not master:
        raise HTTPException(404, "포장 내역을 찾을 수 없습니다.")
    if master.status == "CANCELLED":
        return {"message": "이미 취소된 포장입니다."}
    package_lots = [x.package_lot_no for x in master.boxes if x.package_lot_no]
    for package_lot in package_lots:
        if db.query(ShippingMasterModel.id).filter(ShippingMasterModel.row_json.contains(package_lot, autoescape=True)).first():
            raise HTTPException(409, f"출고 이력에서 사용된 포장 LOT가 있어 취소할 수 없습니다: {package_lot}")
    master.status = "CANCELLED"
    master.cancelled_by = _username(current_user)
    master.cancelled_at = datetime.now()
    db.commit()
    return {"message": "포장을 취소했습니다. 원 생산 LOT 잔량이 복원됩니다."}
