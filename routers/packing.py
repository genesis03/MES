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


def _next_waiting_lot_seq(db: Session, packing_date: str) -> int:
    """출고대기LOT 발번: YYMMDD + 금형번호 01(고정) + 3자리 순번."""
    yymmdd = packing_date.replace("-", "")[2:]
    prefix = f"{yymmdd}01"
    latest = (
        db.query(PackingBox.package_lot_no)
        .filter(PackingBox.package_lot_no.like(prefix + "%"))
        .order_by(PackingBox.package_lot_no.desc())
        .first()
    )
    if not latest:
        return 1
    try:
        return int(str(latest[0])[-3:]) + 1
    except (TypeError, ValueError):
        return 1


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


def _packing_source_parts(db: Session, finished_part_no: str) -> list[str]:
    """포장 대상 완제품의 직속 하위 생산품을 반환합니다.

    FINAL BOM이 있으면 FINAL BOM을 우선 사용하고, 없으면 직속 BOM 전체를 사용합니다.
    BOM이 없는 품번은 기존과 동일하게 자기 품번의 생산 LOT를 사용합니다.
    """
    bom_rows = (
        db.query(ItemBomModel)
        .filter(ItemBomModel.parent_part_no == finished_part_no)
        .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
        .all()
    )
    if not bom_rows:
        return [finished_part_no]

    final_rows = [row for row in bom_rows if str(row.bom_type or "").upper() == "FINAL"]
    source_rows = final_rows or bom_rows

    result = []
    for row in source_rows:
        child = str(row.child_part_no or "").strip()
        if not child or child in result:
            continue
        has_production_lot = (
            db.query(ProductionLotModel.id)
            .filter(
                ProductionLotModel.part_no == child,
                ProductionLotModel.status == "ACTIVE",
            )
            .first()
            is not None
        )
        if has_production_lot:
            result.append(child)

    if result:
        return result

    return [
        str(row.child_part_no).strip()
        for row in source_rows
        if str(row.child_part_no or "").strip()
    ]


def _lots(db: Session, finished_part_no: str):
    source_parts = _packing_source_parts(db, finished_part_no)
    if not source_parts:
        return []

    item_map = {
        item.part_no: item
        for item in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(source_parts)).all()
    }
    rows = (
        db.query(ProductionLotModel)
        .filter(
            ProductionLotModel.part_no.in_(source_parts),
            ProductionLotModel.status == "ACTIVE",
        )
        .order_by(ProductionLotModel.created_at.asc(), ProductionLotModel.id.asc())
        .all()
    )
    result = []
    for row in rows:
        source_item = item_map.get(row.part_no)
        result.append({
            "lot": row,
            "source_part_no": row.part_no,
            "source_part_name": source_item.part_name if source_item else "",
            "available": _lot_available(db, row),
        })
    return result


def _preview(db: Session, finished_part_no: str, scanned_lot_no: str, target_qty: float):
    rows = _lots(db, finished_part_no)
    scanned_row = next(
        (x for x in rows if x["lot"].lot_no.upper() == scanned_lot_no.upper()),
        None,
    )
    if scanned_row is None:
        raise HTTPException(404, "선택 완제품의 하위 품번에 해당하는 사용 가능한 생산 LOT가 아닙니다.")

    source_part_no = scanned_row["source_part_no"]
    source_rows = [x for x in rows if x["source_part_no"] == source_part_no]
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
                "source_part_no": row["source_part_no"],
                "source_part_name": row["source_part_name"],
            })
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

    source_parts = _packing_source_parts(db, item.part_no)
    lot_rows = []
    for row in _lots(db, item.part_no):
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

    lot_map = {x["lot"].lot_no: x["lot"] for x in _lots(db, item.part_no)}
    for row in expected:
        lot = lot_map.get(row["lot_no"])
        master.allocations.append(PackingLotAllocation(
            source_lot_no=row["lot_no"],
            allocated_qty=row["qty"],
            storage_location=lot.storage_location if lot else None,
        ))

    waiting_seq = _next_waiting_lot_seq(db, payload.packing_date)
    yymmdd = payload.packing_date.replace("-", "")[2:]
    for box_no in range(1, payload.box_count + 1):
        waiting_lot_no = f"{yymmdd}01{waiting_seq:03d}"
        master.boxes.append(PackingBox(
            box_no=box_no,
            package_lot_no=waiting_lot_no,
            box_qty=float(payload.box_qty),
        ))
        waiting_seq += 1

    db.commit()
    return {
        "id": master.id,
        "packing_no": master.packing_no,
        "waiting_lots": [box.package_lot_no for box in master.boxes],
        "message": "포장 처리가 완료되었습니다.",
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
    return [{
        "id": x.id,
        "packing_no": x.packing_no,
        "packing_date": x.packing_date,
        "part_no": x.part_no,
        "part_name": x.part_name or "",
        "total_qty": float(x.total_qty or 0),
        "box_count": x.box_count,
        "box_qty": float(x.box_qty or 0),
        "waiting_lots": [b.package_lot_no for b in x.boxes],
    } for x in rows]


@router.post("/api/packing/{packing_id}/cancel")
def cancel_packing(packing_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    master = db.get(PackingMaster, packing_id)
    if not master:
        raise HTTPException(404, "포장 내역을 찾을 수 없습니다.")
    if master.status == "CANCELLED":
        return {"message": "이미 취소된 포장입니다."}

    waiting_lots = [x.package_lot_no for x in master.boxes if x.package_lot_no]
    for waiting_lot in waiting_lots:
        if db.query(ShippingMasterModel.id).filter(ShippingMasterModel.row_json.contains(waiting_lot, autoescape=True)).first():
            raise HTTPException(409, f"출고 이력에서 사용된 출고대기LOT가 있어 취소할 수 없습니다: {waiting_lot}")

    master.status = "CANCELLED"
    master.cancelled_by = _username(current_user)
    master.cancelled_at = datetime.now()
    db.commit()
    return {"message": "포장을 취소했습니다. 원 생산 LOT 잔량이 복원됩니다."}
