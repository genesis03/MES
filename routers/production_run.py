from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.equipment import EquipmentMaster
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.packing import PackingLotAllocation, PackingMaster
from models.models import CommonCodeModel, ItemBomModel, ItemMasterModel, ProcessModel, PurchaseInboundItem, PurchaseInboundMaster
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunDefect, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from models.worker import WorkerMaster, WorkerProcess

router = APIRouter(prefix="/api/production-run", tags=["Production Run"])


class StartRunPayload(BaseModel):
    work_order_id: int = Field(gt=0)
    performance_date: str
    process_code: str
    operator_id: int = Field(gt=0)
    equipment_id: int = Field(gt=0)
    shift_type: str
    performance_type: str = "MACHINING"


class DefectQtyInput(BaseModel):
    defect_type_code: str
    defect_qty: float = Field(ge=0)


class UpdateRunPayload(BaseModel):
    start_time: str
    end_time: Optional[str] = None
    good_qty: float = Field(ge=0, default=0)
    setup_qty: float = Field(ge=0, default=0)
    defects: List[DefectQtyInput] = []
    note: Optional[str] = None


class ScanLotPayload(BaseModel):
    lot_no: str


def _user_name(user) -> str:
    return str(getattr(user, "name", None) or getattr(user, "username", None) or "")


def _get_run(db: Session, run_id: int) -> ProductionRun:
    run = db.get(ProductionRun, run_id)
    if not run:
        raise HTTPException(404, "가동내역을 찾을 수 없습니다.")
    return run


def _bom_rows(db: Session, item_id: int, process_code: str):
    rows = (
        db.query(ItemBomModel)
        .filter(ItemBomModel.parent_item_id == item_id, ItemBomModel.process_code == process_code)
        .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
        .all()
    )
    if not rows:
        rows = (
            db.query(ItemBomModel)
            .filter(ItemBomModel.parent_item_id == item_id)
            .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
            .all()
        )
    return rows


def _lot_rows(db: Session, item_id: int):
    rows = []
    item = db.get(ItemMasterModel, item_id) if item_id else None
    purchases = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.item_id == item_id,
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .all()
    )
    for row, master in purchases:
        rows.append({
            "lot_no": row.internal_lot_no,
            "item_id": row.item_id,
            "part_no": item.part_no if item else row.part_no,
            "base_qty": float(row.inbound_qty or 0),
            "storage_location": row.storage_location or "",
            "source_type": "PURCHASE",
            "fifo_at": master.created_at,
            "fifo_id": row.id,
        })
    productions = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.item_id == item_id, ProductionLotModel.status == "ACTIVE")
        .all()
    )
    for row in productions:
        rows.append({
            "lot_no": row.lot_no,
            "item_id": row.item_id,
            "part_no": item.part_no if item else row.part_no,
            "base_qty": float(row.lot_qty or 0),
            "storage_location": row.storage_location or "",
            "source_type": "PRODUCTION",
            "fifo_at": row.created_at,
            "fifo_id": row.id,
        })
    rows.sort(key=lambda x: (x["fifo_at"] or datetime.min, x["fifo_id"], x["lot_no"]))
    return rows


def _available_qty(
    db: Session,
    lot_no: str,
    base_qty: float,
    current_run_id: Optional[int] = None,
    item_id: Optional[int] = None,
) -> float:
    consumed_process = db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0)).filter(LotConsumptionModel.lot_no == lot_no).scalar() or 0.0
    consumed_relation = db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0)).filter(LotRelationModel.parent_lot_no == lot_no).scalar() or 0.0
    packed = (
        db.query(func.coalesce(func.sum(PackingLotAllocation.allocated_qty), 0.0))
        .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
        .filter(PackingLotAllocation.source_lot_no == lot_no, PackingMaster.status == "PACKED")
        .scalar()
        or 0.0
    )
    subcontract_query = (
        db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(
            SubcontractLotAllocation.lot_no == lot_no,
            SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]),
        )
    )
    if item_id:
        subcontract_query = subcontract_query.filter(SubcontractOrderItem.previous_item_id == item_id)
    reserved_subcontract = subcontract_query.scalar() or 0.0

    sample_query = (
        db.query(func.coalesce(func.sum(SubcontractInboundLot.sample_qty), 0.0))
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.child_lot_no == lot_no,
        )
    )
    if item_id:
        sample_query = sample_query.filter(SubcontractInboundItem.item_id == item_id)
    sample_used = sample_query.scalar() or 0.0

    run_query = (
        db.query(func.coalesce(func.sum(ProductionRunLotAllocation.allocated_qty), 0.0))
        .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
        .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
        .filter(ProductionRunLotAllocation.lot_no == lot_no, ProductionRun.status == "IN_PROGRESS")
    )
    if item_id:
        run_query = run_query.filter(ProductionRunMaterial.material_item_id == item_id)
    if current_run_id:
        run_query = run_query.filter(ProductionRun.id != current_run_id)
    reserved_run = run_query.scalar() or 0.0
    return max(
        float(base_qty)
        - float(consumed_process)
        - float(consumed_relation)
        - float(packed)
        - float(reserved_subcontract)
        - float(sample_used)
        - float(reserved_run),
        0.0,
    )


def _allocate_material_fifo(
    db: Session,
    run: ProductionRun,
    material: ProductionRunMaterial,
    scanned_lot_no: str,
):
    required = float(material.required_qty or 0)
    allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
    remaining = max(required - allocated, 0.0)
    if remaining <= 1e-9:
        return [], 0.0

    lots = _lot_rows(db, material.material_item_id)
    scanned_index = next(
        (index for index, lot in enumerate(lots) if lot["lot_no"].upper() == scanned_lot_no.upper()),
        None,
    )
    if scanned_index is None:
        raise HTTPException(404, f"{material.material_part_no} 품번에 해당하는 LOT가 아닙니다.")

    # 스캔 LOT는 FIFO 상한입니다. 스캔 LOT보다 후행인 LOT는 다음 스캔 전까지 자동 배정하지 않습니다.
    allowed_lots = lots[: scanned_index + 1]
    existing = {x.lot_no: x for x in material.allocations}
    candidates = []
    for lot in allowed_lots:
        current_alloc = float(existing[lot["lot_no"]].allocated_qty or 0) if lot["lot_no"] in existing else 0.0
        available_total = _available_qty(
            db,
            lot["lot_no"],
            lot["base_qty"],
            current_run_id=run.id,
            item_id=material.material_item_id,
        )
        additional_available = max(float(available_total) - current_alloc, 0.0)
        if additional_available > 1e-9:
            candidates.append((lot, additional_available))

    if not candidates:
        raise HTTPException(
            409,
            f"{material.material_part_no}의 스캔 LOT {scanned_lot_no}까지 배정 가능한 잔여수량이 없습니다.",
        )

    assigned = []
    to_assign = remaining
    for lot, available in candidates:
        if to_assign <= 1e-9:
            break
        qty = min(to_assign, available)
        current = existing.get(lot["lot_no"])
        if current:
            current.allocated_qty = float(current.allocated_qty or 0) + qty
        else:
            current = ProductionRunLotAllocation(
                lot_no=lot["lot_no"],
                allocated_qty=qty,
                source_type=lot["source_type"],
                storage_location=lot["storage_location"],
            )
            material.allocations.append(current)
            existing[lot["lot_no"]] = current
        assigned.append((lot["lot_no"], qty))
        to_assign -= qty
    return assigned, sum(qty for _, qty in assigned)


def _serialize_material(material: ProductionRunMaterial):
    allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
    return {
        "id": material.id,
        "item_id": material.material_item_id,
        "part_no": material.material_part_no,
        "part_name": material.material_name or "",
        "unit": material.unit,
        "bom_qty": material.bom_qty,
        "required_qty": material.required_qty,
        "allocated_qty": allocated,
        "remaining_qty": max(float(material.required_qty or 0) - allocated, 0.0),
        "complete": abs(allocated - float(material.required_qty or 0)) < 1e-9 and float(material.required_qty or 0) > 0,
        "allocations": [
            {
                "lot_no": x.lot_no,
                "allocated_qty": x.allocated_qty,
                "source_type": x.source_type or "",
                "storage_location": x.storage_location or "",
            }
            for x in material.allocations
        ],
    }


def _serialize_run(run: ProductionRun, db: Optional[Session] = None):
    work_order = getattr(run, "work_order", None)
    part_no = work_order.part_no if work_order else ""
    process_name = run.process_code
    part_name = ""
    output_lot_no = ""
    if db is not None:
        process = db.query(ProcessModel).filter(ProcessModel.process_code == run.process_code).first()
        if process and process.process_name:
            process_name = process.process_name
        if work_order and work_order.item_id:
            item = db.get(ItemMasterModel, work_order.item_id)
            if item:
                part_no = item.part_no
                part_name = item.part_name or ""
        if run.performance_id:
            marker = f"PERF:{run.performance_id}|"
            output_lots = (
                db.query(ProductionLotModel.lot_no)
                .filter(ProductionLotModel.note.like(marker + "%"))
                .order_by(ProductionLotModel.id.asc())
                .all()
            )
            output_lot_no = ", ".join(row[0] for row in output_lots if row[0])
    status_names = {"IN_PROGRESS": "생산중", "COMPLETED": "완료", "CANCELLED": "취소"}
    return {
        "id": run.id,
        "work_order_id": run.work_order_id,
        "work_order_no": work_order.work_order_no if work_order else "",
        "part_no": part_no,
        "part_name": part_name,
        "performance_id": run.performance_id,
        "output_lot_no": output_lot_no,
        "performance_type": run.performance_type or "MACHINING",
        "performance_type_name": "조립" if run.performance_type == "ASSEMBLY" else "가공",
        "performance_date": run.performance_date,
        "process_code": run.process_code,
        "process_name": process_name,
        "operator_id": run.operator_id,
        "operator_name": run.operator_name,
        "equipment_id": run.equipment_id,
        "equipment_code": run.equipment_code,
        "equipment_name": run.equipment_name,
        "shift_type": run.shift_type,
        "shift_name": "주간" if run.shift_type == "DAY" else "야간",
        "start_time": run.start_time,
        "end_time": run.end_time or "",
        "good_qty": run.good_qty,
        "defect_qty": run.defect_qty,
        "setup_qty": run.setup_qty,
        "status": run.status,
        "status_name": status_names.get(run.status, run.status),
        "note": run.note or "",
        "materials": [_serialize_material(x) for x in run.materials],
        "defects": [{"code": x.defect_type_code, "name": x.defect_type_name, "qty": x.defect_qty} for x in run.defects],
    }


@router.get("/orders")
def selectable_orders(process_code: str = Query(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    code = process_code.strip()
    orders = (
        db.query(ProductionWorkOrder)
        .filter(ProductionWorkOrder.status.in_(["WAITING", "IN_PROGRESS"]))
        .order_by(ProductionWorkOrder.priority.asc(), ProductionWorkOrder.order_date.asc(), ProductionWorkOrder.id.asc())
        .all()
    )
    result = []
    for order in orders:
        item = db.get(ItemMasterModel, order.item_id) if order.item_id else None
        # 작업지시 입력 화면과 동일하게, 해당 품목이 BOM의 자품번으로 등록된
        # 공정을 우선 사용하고 품목마스터 생산공정을 보조 기준으로 사용합니다.
        process_match = (
            db.query(ItemBomModel.id)
            .filter(
                ItemBomModel.child_item_id == order.item_id,
                ItemBomModel.process_code == code,
            )
            .first()
        )
        if not process_match and (not item or (item.production_loc or "") != code):
            continue
        result.append({
            "id": order.id,
            "work_order_no": order.work_order_no,
            "item_id": order.item_id,
            "part_no": item.part_no if item else order.part_no,
            "part_name": item.part_name if item else "",
            "order_qty": order.order_qty,
            "production_qty": order.production_qty,
            "remaining_qty": max(float(order.order_qty or 0) - float(order.production_qty or 0), 0.0),
            "priority": order.priority,
            "status": order.status,
            "status_name": {"WAITING": "대기", "IN_PROGRESS": "생산중"}.get(order.status, order.status),
        })
    return result


@router.get("/defect-types")
def defect_types(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = (
        db.query(CommonCodeModel)
        .filter(CommonCodeModel.group_code == "DEFECT_TYPE", CommonCodeModel.is_active == "Y")
        .order_by(CommonCodeModel.sort_order.asc(), CommonCodeModel.id.asc())
        .all()
    )
    return [{"code": x.code, "name": x.code_name} for x in rows]


@router.get("/runs")
def list_runs(
    status: Optional[str] = Query("IN_PROGRESS"),
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    process_code: Optional[str] = Query(None),
    equipment_id: Optional[int] = Query(None),
    performance_type: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ProductionRun)
    if status:
        run_status = status.strip().upper()
        if run_status not in {"IN_PROGRESS", "COMPLETED", "CANCELLED"}:
            raise HTTPException(400, "지원하지 않는 가동 상태입니다.")
        query = query.filter(ProductionRun.status == run_status)
    if start_date:
        query = query.filter(ProductionRun.performance_date >= start_date)
    if end_date:
        query = query.filter(ProductionRun.performance_date <= end_date)
    if process_code:
        query = query.filter(ProductionRun.process_code == process_code.strip())
    if equipment_id:
        query = query.filter(ProductionRun.equipment_id == equipment_id)
    if performance_type:
        run_type = performance_type.strip().upper()
        if run_type not in {"MACHINING", "ASSEMBLY"}:
            raise HTTPException(400, "지원하지 않는 생산실적 구분입니다.")
        query = query.filter(ProductionRun.performance_type == run_type)
    rows = query.order_by(ProductionRun.created_at.desc(), ProductionRun.id.desc()).limit(limit).all()
    for row in rows:
        row.work_order = db.get(ProductionWorkOrder, row.work_order_id)
    return [_serialize_run(x, db) for x in rows]


@router.post("/start")
def start_run(payload: StartRunPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order = db.get(ProductionWorkOrder, payload.work_order_id)
    if not order or order.status not in {"WAITING", "IN_PROGRESS"}:
        raise HTTPException(400, "선택할 수 없는 작업지시입니다.")
    run_type = payload.performance_type.strip().upper()
    if run_type not in {"MACHINING", "ASSEMBLY"}:
        raise HTTPException(400, "가공 또는 조립 실적 구분이 올바르지 않습니다.")
    process = db.query(ProcessModel).filter(ProcessModel.process_code == payload.process_code.strip(), ProcessModel.is_active == "Y").first()
    if not process:
        raise HTTPException(400, "사용 가능한 공정을 선택하세요.")
    worker = (
        db.query(WorkerMaster)
        .join(WorkerProcess, WorkerProcess.worker_id == WorkerMaster.id)
        .filter(WorkerMaster.id == payload.operator_id, WorkerMaster.is_active == "Y", WorkerProcess.process_code == process.process_code)
        .first()
    )
    if not worker:
        raise HTTPException(400, "해당 공정에 등록된 작업자를 선택하세요.")
    equipment = db.query(EquipmentMaster).filter(EquipmentMaster.id == payload.equipment_id, EquipmentMaster.is_active == "Y", EquipmentMaster.process_code == process.process_code).first()
    if not equipment:
        raise HTTPException(400, "해당 공정에 등록된 생산호기를 선택하세요.")
    shift = payload.shift_type.strip().upper()
    if shift not in {"DAY", "NIGHT"}:
        raise HTTPException(400, "주간 또는 야간을 선택하세요.")
    existing = db.query(ProductionRun.id).filter(ProductionRun.work_order_id == order.id, ProductionRun.status == "IN_PROGRESS").first()
    if existing:
        raise HTTPException(409, "이 작업지시는 이미 생산중 가동내역이 있습니다.")

    now_text = datetime.now().strftime("%Y-%m-%d %H:%M")
    run = ProductionRun(
        work_order_id=order.id,
        performance_type=run_type,
        performance_date=payload.performance_date,
        process_code=process.process_code,
        operator_id=worker.id,
        operator_name=worker.worker_name,
        equipment_id=equipment.id,
        equipment_code=equipment.equipment_code,
        equipment_name=equipment.equipment_name,
        shift_type=shift,
        start_time=now_text,
        status="IN_PROGRESS",
        created_by=_user_name(current_user),
    )
    db.add(run)
    db.flush()

    bom_rows = _bom_rows(db, order.item_id, process.process_code)
    if not bom_rows:
        db.rollback()
        raise HTTPException(400, "작업지시 품번의 BOM 자재를 찾을 수 없습니다.")
    child_ids = {int(x.child_item_id) for x in bom_rows if x.child_item_id}
    item_map = {x.id: x for x in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(child_ids)).all()} if child_ids else {}
    grouped = {}
    for bom in bom_rows:
        if not bom.child_item_id:
            continue
        key = int(bom.child_item_id)
        if key not in grouped:
            grouped[key] = {"qty": 0.0, "unit": bom.unit or "EA", "sort": int(bom.sort_order or 1)}
        grouped[key]["qty"] += float(bom.quantity or 0)
    for item_id, data in grouped.items():
        item = item_map.get(item_id)
        db.add(ProductionRunMaterial(
            run_id=run.id,
            material_item_id=item_id,
            material_part_no=item.part_no if item else "",
            material_name=item.part_name if item else "",
            unit=data["unit"],
            bom_qty=data["qty"],
            required_qty=0.0,
            sort_order=data["sort"],
        ))
    order.status = "IN_PROGRESS"
    db.commit()
    run = _get_run(db, run.id)
    run.work_order = order
    return _serialize_run(run, db)


@router.get("/{run_id}/materials/{material_id}/lots")
def material_lots(
    run_id: int,
    material_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = _get_run(db, run_id)
    material = db.get(ProductionRunMaterial, material_id)
    if material is None or material.run_id != run.id:
        raise HTTPException(404, "BOM 자재를 찾을 수 없습니다.")

    current_alloc_by_lot = {}
    for row in material.allocations:
        current_alloc_by_lot[row.lot_no] = current_alloc_by_lot.get(row.lot_no, 0.0) + float(row.allocated_qty or 0)

    rows = []
    fifo_enabled = True
    for lot in _lot_rows(db, material.material_item_id):
        current_alloc = current_alloc_by_lot.get(lot["lot_no"], 0.0)
        available_before_current = _available_qty(
            db,
            lot["lot_no"],
            lot["base_qty"],
            current_run_id=run.id,
            item_id=material.material_item_id,
        )
        additional_available = max(float(available_before_current) - current_alloc, 0.0)
        already_allocated = current_alloc > 1e-9
        can_allocate = (
            run.status == "IN_PROGRESS"
            and not already_allocated
            and additional_available > 1e-9
            and fifo_enabled
            and float(material.required_qty or 0) - sum(float(x.allocated_qty or 0) for x in material.allocations) > 1e-9
        )
        if can_allocate:
            fifo_enabled = False
        rows.append({
            "lot_no": lot["lot_no"],
            "lot_qty": float(lot["base_qty"] or 0),
            "remaining_qty": additional_available,
            "allocated_qty": current_alloc,
            "source_type": lot["source_type"],
            "storage_location": lot["storage_location"],
            "can_allocate": can_allocate,
            "fifo_wait": (not already_allocated and additional_available > 1e-9 and not can_allocate),
        })

    return {
        "material": _serialize_material(material),
        "lots": rows,
    }


@router.delete("/{run_id}/materials/{material_id}/allocations")
def clear_material_allocations(
    run_id: int,
    material_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = _get_run(db, run_id)
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "생산중 가동내역만 LOT 배정을 초기화할 수 있습니다.")
    material = db.get(ProductionRunMaterial, material_id)
    if material is None or material.run_id != run.id:
        raise HTTPException(404, "BOM 자재를 찾을 수 없습니다.")
    material.allocations.clear()
    db.commit()
    return {"message": f"{material.material_part_no} LOT 배정을 초기화했습니다."}


@router.post("/{run_id}/materials/{material_id}/scan-lot")
def scan_material_lot(
    run_id: int,
    material_id: int,
    payload: ScanLotPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = _get_run(db, run_id)
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "생산중 가동내역만 LOT를 배정할 수 있습니다.")

    material = db.get(ProductionRunMaterial, material_id)
    if material is None or material.run_id != run.id:
        raise HTTPException(404, "BOM 자재를 찾을 수 없습니다.")

    scanned = payload.lot_no.strip()
    if not scanned:
        raise HTTPException(400, "LOT 번호를 입력하세요.")

    lots = _lot_rows(db, material.material_item_id)
    scanned_lot = next((lot for lot in lots if lot["lot_no"].upper() == scanned.upper()), None)
    if scanned_lot is None:
        raise HTTPException(404, f"{material.material_part_no} 품번에 해당하는 LOT가 아닙니다.")

    assigned, assigned_qty = _allocate_material_fifo(db, run, material, scanned)
    if not assigned:
        raise HTTPException(409, f"{material.material_part_no}는 이미 필요수량이 모두 배정되었습니다.")
    db.commit()

    allocation_text = ", ".join(f"{lot_no} {qty:g}" for lot_no, qty in assigned)
    first_lot = assigned[0][0]
    if first_lot.upper() != scanned.upper():
        message = f"스캔 LOT {scanned}까지의 선행 LOT를 FIFO 기준으로 {allocation_text} 배정했습니다."
    else:
        message = f"스캔 LOT {scanned}까지 FIFO 기준으로 {allocation_text} 배정했습니다."
    return {"message": message, "material": _serialize_material(material), "assigned_qty": assigned_qty}


@router.get("/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    run = _get_run(db, run_id)
    run.work_order = db.get(ProductionWorkOrder, run.work_order_id)
    return _serialize_run(run, db)


@router.put("/{run_id}/details")
def update_run_details(run_id: int, payload: UpdateRunPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    run = _get_run(db, run_id)
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "생산중 가동내역만 수정할 수 있습니다.")
    if not payload.start_time.strip():
        raise HTTPException(400, "시작시간을 입력하세요.")
    run.start_time = payload.start_time.strip()
    run.end_time = (payload.end_time or "").strip() or None
    run.good_qty = float(payload.good_qty or 0)
    run.setup_qty = float(payload.setup_qty or 0)
    run.note = (payload.note or "").strip() or None

    db.query(ProductionRunDefect).filter(ProductionRunDefect.run_id == run.id).delete(synchronize_session=False)
    db.flush()

    defect_qty_by_code = {}
    for row in payload.defects:
        if row.defect_qty <= 0:
            continue
        code = row.defect_type_code.strip()
        if not code:
            continue
        defect_qty_by_code[code] = defect_qty_by_code.get(code, 0.0) + float(row.defect_qty)

    defect_total = 0.0
    code_map = {}
    if defect_qty_by_code:
        codes = list(defect_qty_by_code.keys())
        code_map = {
            x.code: x
            for x in db.query(CommonCodeModel)
            .filter(
                CommonCodeModel.group_code == "DEFECT_TYPE",
                CommonCodeModel.code.in_(codes),
                CommonCodeModel.is_active == "Y",
            )
            .all()
        }

    for code, defect_qty in defect_qty_by_code.items():
        master = code_map.get(code)
        if not master:
            raise HTTPException(400, f"사용할 수 없는 불량유형입니다: {code}")
        db.add(
            ProductionRunDefect(
                run_id=run.id,
                defect_type_code=code,
                defect_type_name=master.code_name,
                defect_qty=defect_qty,
            )
        )
        defect_total += defect_qty
    run.defect_qty = defect_total

    process_qty = run.good_qty + run.defect_qty + run.setup_qty
    for material in run.materials:
        material.required_qty = process_qty * float(material.bom_qty or 0)
        allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
        if allocated > material.required_qty + 1e-9:
            raise HTTPException(409, f"{material.material_part_no}의 기존 LOT 배정량이 새 필요수량보다 큽니다. LOT 배정을 초기화한 뒤 다시 진행하세요.")
    db.commit()
    run = _get_run(db, run.id)
    run.work_order = db.get(ProductionWorkOrder, run.work_order_id)
    return _serialize_run(run, db)


@router.delete("/{run_id}/allocations")
def clear_allocations(run_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    run = _get_run(db, run_id)
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "생산중 가동내역만 LOT 배정을 초기화할 수 있습니다.")
    for material in run.materials:
        material.allocations.clear()
    db.commit()
    return {"message": "LOT 배정을 초기화했습니다."}


@router.post("/{run_id}/scan-lot")
def scan_lot(run_id: int, payload: ScanLotPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    run = _get_run(db, run_id)
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "생산중 가동내역만 LOT를 배정할 수 있습니다.")
    scanned = payload.lot_no.strip()
    if not scanned:
        raise HTTPException(400, "LOT 번호를 입력하세요.")

    matched_material = None
    matched_lot = None
    for material in run.materials:
        for lot in _lot_rows(db, material.material_item_id):
            if lot["lot_no"].upper() == scanned.upper():
                matched_material = material
                matched_lot = lot
                break
        if matched_material:
            break
    if not matched_material or not matched_lot:
        raise HTTPException(404, "이 작업의 BOM 자재에 해당하는 LOT가 아닙니다.")

    assigned, assigned_qty = _allocate_material_fifo(db, run, matched_material, scanned)
    if not assigned:
        raise HTTPException(409, f"{matched_material.material_part_no}는 이미 필요수량이 모두 배정되었습니다.")
    db.commit()

    allocation_text = ", ".join(f"{lot_no} {qty:g}" for lot_no, qty in assigned)
    first_lot = assigned[0][0]
    if first_lot.upper() != scanned.upper():
        message = f"스캔 LOT {scanned}를 확인하고 FIFO 기준으로 {allocation_text} 배정했습니다."
    else:
        message = f"FIFO 기준으로 {allocation_text} 배정했습니다."
    return {"message": message, "material": _serialize_material(matched_material), "assigned_qty": assigned_qty}


@router.post("/{run_id}/complete")
def complete_run(run_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    run = _get_run(db, run_id)
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "이미 종료된 가동내역입니다.")
    if not run.end_time:
        raise HTTPException(400, "종료시간을 입력하세요.")
    if float(run.good_qty or 0) <= 0:
        raise HTTPException(400, "양품수량을 입력하세요.")
    process_qty = float(run.good_qty or 0) + float(run.defect_qty or 0) + float(run.setup_qty or 0)
    if process_qty <= 0:
        raise HTTPException(400, "처리수량이 없습니다.")

    incomplete = []
    for material in run.materials:
        allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
        required = float(material.required_qty or 0)
        if abs(allocated - required) > 1e-9:
            incomplete.append(f"{material.material_part_no} 필요 {required:g} / 배정 {allocated:g}")
    if incomplete:
        raise HTTPException(409, "BOM 자재 LOT 배정이 완료되지 않았습니다: " + "; ".join(incomplete))

    order = db.get(ProductionWorkOrder, run.work_order_id)
    if not order:
        raise HTTPException(404, "작업지시를 찾을 수 없습니다.")
    lot_nos = [a.lot_no for m in run.materials for a in m.allocations]
    total_consumed = sum(float(a.allocated_qty or 0) for m in run.materials for a in m.allocations)
    perf = ProductionPerformance(
        work_order_id=order.id,
        performance_type=run.performance_type or "MACHINING",
        performance_date=run.performance_date,
        process_code=run.process_code,
        shift_type=run.shift_type,
        operator_id=run.operator_id,
        operator_name=run.operator_name,
        equipment_id=run.equipment_id,
        equipment_code=run.equipment_code,
        equipment_name=run.equipment_name,
        source_lot_no=",".join(lot_nos),
        good_qty=run.good_qty,
        defect_qty=run.defect_qty,
        setup_qty=run.setup_qty,
        consumed_qty=total_consumed,
        note=run.note,
        created_by=_user_name(current_user),
    )
    db.add(perf)
    db.flush()
    for material in run.materials:
        for allocation in material.allocations:
            db.add(LotConsumptionModel(
                lot_no=allocation.lot_no,
                item_id=material.material_item_id,
                part_no=material.material_part_no,
                work_order_id=order.id,
                performance_id=perf.id,
                process_code=run.process_code,
                consumed_qty=allocation.allocated_qty,
            ))
    order.production_qty = float(order.production_qty or 0) + float(run.good_qty or 0)
    run.performance_id = perf.id
    run.status = "COMPLETED"
    db.commit()
    return {"message": "생산실적이 등록되고 BOM 자재 LOT가 차감되었습니다.", "performance_id": perf.id, "production_qty": order.production_qty}
