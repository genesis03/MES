from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user, require_admin_user
from models.inventory_adjustment import InventoryAdjustmentModel
from models.inventory_movement import InventoryMovementModel
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import CommonCodeModel, ItemMasterModel, ProcessModel, StorageLocationModel
from models.packing import PackingLotAllocation, PackingMaster
from models.production import ProductionPerformance
from models.production_defect import QualityProductionDefect, QualityProductionDefectDetail
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from services.production_defect_service import active_production_defect_qty
from services.production_lot_service import performance_id_from_lot_note

router = APIRouter(prefix="/api/quality/production-defects", tags=["Quality Production Defects"])


class ProductionDefectItemInput(BaseModel):
    defect_type_code: str = Field(min_length=1, max_length=30)
    defect_qty: float = Field(gt=0)


class ProductionDefectCreateInput(BaseModel):
    production_lot_id: int = Field(gt=0)
    defect_date: str = Field(min_length=10, max_length=10)
    defect_items: List[ProductionDefectItemInput] = Field(min_length=1)
    remark: Optional[str] = Field(default=None, max_length=1000)


class ProductionDefectCancelInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _username(user) -> str:
    return str(getattr(user, "username", None) or getattr(user, "name", None) or "").strip()


def _validate_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "불량일자가 올바르지 않습니다.")
    return value


def _defect_types(db: Session):
    return (
        db.query(CommonCodeModel)
        .filter(
            CommonCodeModel.group_code == "DEFECT_TYPE",
            CommonCodeModel.is_active == "Y",
        )
        .order_by(CommonCodeModel.sort_order.asc(), CommonCodeModel.id.asc())
        .all()
    )


def _adjustment_qty(db: Session, lot_no: str, item_id: int) -> float:
    return float(
        db.query(func.coalesce(func.sum(InventoryAdjustmentModel.adjustment_qty), 0.0))
        .filter(
            InventoryAdjustmentModel.lot_no == lot_no,
            InventoryAdjustmentModel.item_id == item_id,
        )
        .scalar()
        or 0.0
    )


def _used_qty_without_production_defect(db: Session, lot_no: str, item_id: int) -> float:
    consumed = float(
        db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0))
        .filter(LotConsumptionModel.lot_no == lot_no)
        .scalar()
        or 0.0
    )
    related = float(
        db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0))
        .filter(LotRelationModel.parent_lot_no == lot_no)
        .scalar()
        or 0.0
    )
    packed = float(
        db.query(func.coalesce(func.sum(PackingLotAllocation.allocated_qty), 0.0))
        .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
        .filter(
            PackingLotAllocation.source_lot_no == lot_no,
            PackingMaster.status == "PACKED",
        )
        .scalar()
        or 0.0
    )
    subcontract_reserved = float(
        db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(
            SubcontractLotAllocation.lot_no == lot_no,
            SubcontractOrderItem.previous_item_id == item_id,
            SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]),
        )
        .scalar()
        or 0.0
    )
    sample_used = float(
        db.query(func.coalesce(func.sum(SubcontractInboundLot.sample_qty), 0.0))
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.child_lot_no == lot_no,
            SubcontractInboundItem.item_id == item_id,
        )
        .scalar()
        or 0.0
    )
    reserved_run = float(
        db.query(func.coalesce(func.sum(ProductionRunLotAllocation.allocated_qty), 0.0))
        .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
        .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
        .filter(
            ProductionRunLotAllocation.lot_no == lot_no,
            ProductionRunMaterial.material_item_id == item_id,
            ProductionRun.status == "IN_PROGRESS",
        )
        .scalar()
        or 0.0
    )
    return consumed + related + packed + subcontract_reserved + sample_used + reserved_run


def _available_qty(db: Session, lot: ProductionLotModel) -> float:
    return max(
        float(lot.lot_qty or 0)
        - _used_qty_without_production_defect(db, lot.lot_no, lot.item_id)
        - active_production_defect_qty(db, lot.lot_no, lot.item_id)
        + _adjustment_qty(db, lot.lot_no, lot.item_id),
        0.0,
    )


def _current_storage(db: Session, lot: ProductionLotModel) -> tuple[str, str]:
    code = str(lot.storage_location or "").strip()
    movement = (
        db.query(InventoryMovementModel)
        .filter(InventoryMovementModel.lot_no == lot.lot_no)
        .order_by(InventoryMovementModel.created_at.desc(), InventoryMovementModel.id.desc())
        .first()
    )
    if movement and movement.to_location:
        code = movement.to_location
    location = (
        db.query(StorageLocationModel)
        .filter(StorageLocationModel.location_code == code)
        .first()
        if code else None
    )
    return code, (location.location_name if location else code)


def _lot_row(db: Session, lot: ProductionLotModel) -> dict:
    item = db.get(ItemMasterModel, lot.item_id) if lot.item_id else None
    performance_id = performance_id_from_lot_note(lot.note)
    performance = db.get(ProductionPerformance, performance_id) if performance_id else None
    process_code = performance.process_code if performance else ""
    process = (
        db.query(ProcessModel)
        .filter(ProcessModel.process_code == process_code)
        .first()
        if process_code else None
    )
    storage_code, storage_name = _current_storage(db, lot)
    return {
        "production_lot_id": lot.id,
        "item_id": lot.item_id,
        "part_no": item.part_no if item else lot.part_no,
        "part_name": item.part_name if item else "",
        "lot_no": lot.lot_no,
        "lot_qty": float(lot.lot_qty or 0),
        "available_qty": _available_qty(db, lot),
        "storage_location": storage_code,
        "storage_location_name": storage_name,
        "process_code": process_code,
        "process_name": process.process_name if process else process_code,
        "performance_id": performance_id,
        "performance_date": performance.performance_date if performance else "",
        "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
        "unit": item.unit if item else "EA",
    }


@router.get("/options")
def production_defect_options(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    return {
        "defect_types": [
            {"code": row.code, "name": row.code_name}
            for row in _defect_types(db)
        ]
    }


@router.get("/lots")
def production_defect_lots(
    keyword: Optional[str] = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    value = str(keyword or "").strip()
    query = (
        db.query(ProductionLotModel)
        .filter(
            ProductionLotModel.status == "ACTIVE",
            ProductionLotModel.note.like("PERF:%"),
        )
        .order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc())
    )
    if value:
        item_ids = [
            row.id
            for row in db.query(ItemMasterModel.id)
            .filter(
                (ItemMasterModel.part_no.ilike(f"%{value}%"))
                | (ItemMasterModel.part_name.ilike(f"%{value}%"))
            )
            .all()
        ]
        filters = [ProductionLotModel.lot_no.ilike(f"%{value}%")]
        if item_ids:
            filters.append(ProductionLotModel.item_id.in_(item_ids))
        from sqlalchemy import or_
        query = query.filter(or_(*filters))

    rows = []
    for lot in query.limit(limit).all():
        row = _lot_row(db, lot)
        if not row["performance_id"]:
            continue
        if float(row["available_qty"] or 0) <= 1e-9:
            continue
        rows.append(row)
    return {"items": rows, "total": len(rows)}


@router.post("")
def create_production_defect(
    payload: ProductionDefectCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    defect_date = _validate_date(payload.defect_date)
    lot = db.get(ProductionLotModel, payload.production_lot_id)
    if lot is None or lot.status != "ACTIVE":
        raise HTTPException(404, "불량 처리할 생산 LOT를 찾을 수 없습니다.")
    performance_id = performance_id_from_lot_note(lot.note)
    if not performance_id or db.get(ProductionPerformance, performance_id) is None:
        raise HTTPException(409, "생산실적에서 생성된 LOT만 불량 처리할 수 있습니다.")

    allowed = {row.code for row in _defect_types(db)}
    seen = set()
    details = []
    total = 0.0
    for item in payload.defect_items:
        code = item.defect_type_code.strip()
        if code not in allowed:
            raise HTTPException(422, f"사용할 수 없는 불량유형입니다: {code}")
        if code in seen:
            raise HTTPException(422, "동일 불량유형이 중복되었습니다.")
        seen.add(code)
        qty = float(item.defect_qty)
        total += qty
        details.append((code, qty))

    available = _available_qty(db, lot)
    if total > available + 1e-9:
        raise HTTPException(
            409,
            f"불량합계 {total:g}이 현재 처리 가능 수량 {available:g}보다 큽니다.",
        )

    row = QualityProductionDefect(
        production_lot_id=lot.id,
        item_id=lot.item_id,
        lot_no=lot.lot_no,
        lot_qty=float(lot.lot_qty or 0),
        available_qty_before=available,
        defect_date=defect_date,
        defect_qty=total,
        status="ACTIVE",
        remark=(payload.remark or "").strip() or None,
        created_by=_username(current_user) or None,
    )
    db.add(row)
    db.flush()
    for code, qty in details:
        db.add(
            QualityProductionDefectDetail(
                defect_id=row.id,
                defect_type_code=code,
                defect_qty=qty,
            )
        )
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "lot_no": row.lot_no,
        "defect_qty": float(row.defect_qty or 0),
        "remaining_qty": _available_qty(db, lot),
        "status": row.status,
    }


@router.get("/history")
def production_defect_history(
    lot_no: Optional[str] = Query(None, max_length=100),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(QualityProductionDefect)
    if lot_no and lot_no.strip():
        query = query.filter(QualityProductionDefect.lot_no.contains(lot_no.strip(), autoescape=True))
    rows = (
        query.order_by(
            QualityProductionDefect.created_at.desc(),
            QualityProductionDefect.id.desc(),
        )
        .limit(limit)
        .all()
    )
    defect_ids = [row.id for row in rows]
    detail_rows = (
        db.query(QualityProductionDefectDetail)
        .filter(QualityProductionDefectDetail.defect_id.in_(defect_ids))
        .order_by(QualityProductionDefectDetail.id.asc())
        .all()
        if defect_ids else []
    )
    names = {row.code: row.code_name for row in _defect_types(db)}
    detail_map = {}
    for detail in detail_rows:
        detail_map.setdefault(detail.defect_id, []).append({
            "code": detail.defect_type_code,
            "name": names.get(detail.defect_type_code, detail.defect_type_code),
            "qty": float(detail.defect_qty or 0),
        })
    item_ids = list({row.item_id for row in rows})
    items = {
        row.id: row
        for row in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all()
    } if item_ids else {}
    return {
        "items": [
            {
                "id": row.id,
                "item_id": row.item_id,
                "part_no": items[row.item_id].part_no if row.item_id in items else "",
                "part_name": items[row.item_id].part_name if row.item_id in items else "",
                "lot_no": row.lot_no,
                "lot_qty": float(row.lot_qty or 0),
                "available_qty_before": float(row.available_qty_before or 0),
                "defect_date": row.defect_date,
                "defect_qty": float(row.defect_qty or 0),
                "status": row.status,
                "remark": row.remark or "",
                "created_by": row.created_by or "",
                "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
                "cancelled_by": row.cancelled_by or "",
                "cancelled_at": row.cancelled_at.strftime("%Y-%m-%d %H:%M:%S") if row.cancelled_at else "",
                "cancel_reason": row.cancel_reason or "",
                "defect_items": detail_map.get(row.id, []),
            }
            for row in rows
        ]
    }


@router.post("/{defect_id}/cancel")
def cancel_production_defect(
    defect_id: int,
    payload: ProductionDefectCancelInput,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin_user),
):
    row = db.get(QualityProductionDefect, defect_id)
    if row is None:
        raise HTTPException(404, "생산 LOT 불량 등록 내역을 찾을 수 없습니다.")
    if row.status != "ACTIVE":
        raise HTTPException(409, "이미 취소된 불량 등록입니다.")

    row.status = "CANCELLED"
    row.cancelled_by = _username(current_user) or None
    row.cancelled_at = datetime.now()
    row.cancel_reason = payload.reason.strip()
    db.commit()
    db.refresh(row)

    lot = db.get(ProductionLotModel, row.production_lot_id)
    return {
        "id": row.id,
        "status": row.status,
        "restored_available_qty": _available_qty(db, lot) if lot else None,
    }
