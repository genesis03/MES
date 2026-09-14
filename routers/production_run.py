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
from models.models import CommonCodeModel, ItemBomModel, ItemMasterModel, ProcessModel, PurchaseInboundItem, PurchaseInboundMaster
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunDefect, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.worker import WorkerMaster, WorkerProcess

router = APIRouter(prefix="/api/production-run", tags=["Production Run"])


class StartRunPayload(BaseModel):
    work_order_id: int = Field(gt=0)
    performance_date: str
    process_code: str
    operator_id: int = Field(gt=0)
    equipment_id: int = Field(gt=0)
    shift_type: str


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


def _bom_rows(db: Session, part_no: str, process_code: str):
    rows = (
        db.query(ItemBomModel)
        .filter(ItemBomModel.parent_part_no == part_no, ItemBomModel.process_code == process_code)
        .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
        .all()
    )
    if not rows:
        rows = (
            db.query(ItemBomModel)
            .filter(ItemBomModel.parent_part_no == part_no)
            .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
            .all()
        )
    return rows


def _lot_rows(db: Session, part_no: str):
    rows = []
    purchases = (
        db.query(PurchaseInboundItem)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.part_no == part_no,
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .all()
    )
    for row in purchases:
        rows.append({
            "lot_no": row.internal_lot_no,
            "part_no": row.part_no,
            "base_qty": float(row.inbound_qty or 0),
            "storage_location": row.storage_location or "",
            "source_type": "PURCHASE",
        })
    productions = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.part_no == part_no, ProductionLotModel.status == "ACTIVE")
        .all()
    )
    for row in productions:
        rows.append({
            "lot_no": row.lot_no,
            "part_no": row.part_no,
            "base_qty": float(row.lot_qty or 0),
            "storage_location": row.storage_location or "",
            "source_type": "PRODUCTION",
        })
    rows.sort(key=lambda x: x["lot_no"])
    return rows


def _available_qty(db: Session, lot_no: str, base_qty: float, current_run_id: Optional[int] = None) -> float:
    consumed_process = db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0)).filter(LotConsumptionModel.lot_no == lot_no).scalar() or 0.0
    consumed_relation = db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0)).filter(LotRelationModel.parent_lot_no == lot_no).scalar() or 0.0
    reserved_subcontract = (
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
    run_query = (
        db.query(func.coalesce(func.sum(ProductionRunLotAllocation.allocated_qty), 0.0))
        .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
        .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
        .filter(ProductionRunLotAllocation.lot_no == lot_no, ProductionRun.status == "IN_PROGRESS")
    )
    if current_run_id:
        run_query = run_query.filter(ProductionRun.id != current_run_id)
    reserved_run = run_query.scalar() or 0.0
    return max(float(base_qty) - float(consumed_process) - float(consumed_relation) - float(reserved_subcontract) - float(reserved_run), 0.0)


def _serialize_material(material: ProductionRunMaterial):
    allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
    return {
        "id": material.id,
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


def _serialize_run(run: ProductionRun):
    return {
        "id": run.id,
        "work_order_id": run.work_order_id,
        "work_order_no": run.work_order.work_order_no if getattr(run, "work_order", None) else "",
        "part_no": run.work_order.part_no if getattr(run, "work_order", None) else "",
        "performance_date": run.performance_date,
        "process_code": run.process_code,
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
        item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == order.part_no).first()
        process_match = db.query(ItemBomModel.id).filter(ItemBomModel.parent_part_no == order.part_no, ItemBomModel.process_code == code).first()
        if not process_match and (not item or (item.production_loc or "") != code):
            continue
        result.append({
            "id": order.id,
            "work_order_no": order.work_order_no,
            "part_no": order.part_no,
            "part_name": item.part_name if item else "",
            "order_qty": order.order_qty,
            "production_qty": order.production_qty,
            "remaining_qty": max(float(order.order_qty or 0) - float(order.production_qty or 0), 0.0),
            "priority": order.priority,
            "status": order.status,
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
def list_runs(status: Optional[str] = Query("IN_PROGRESS"), process_code: Optional[str] = Query(None), equipment_id: Optional[int] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(ProductionRun)
    if status:
        query = query.filter(ProductionRun.status == status)
    if process_code:
        query = query.filter(ProductionRun.process_code == process_code.strip())
    if equipment_id:
        query = query.filter(ProductionRun.equipment_id == equipment_id)
    rows = query.order_by(ProductionRun.created_at.desc()).limit(200).all()
    for row in rows:
        row.work_order = db.get(ProductionWorkOrder, row.work_order_id)
    return [_serialize_run(x) for x in rows]


@router.post("/start")
def start_run(payload: StartRunPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order = db.get(ProductionWorkOrder, payload.work_order_id)
    if not order or order.status not in {"WAITING", "IN_PROGRESS"}:
        raise HTTPException(400, "선택할 수 없는 작업지시입니다.")
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

    bom_rows = _bom_rows(db, order.part_no, process.process_code)
    if not bom_rows:
        db.rollback()
        raise HTTPException(400, "작업지시 품번의 BOM 자재를 찾을 수 없습니다.")
    item_map = {x.part_no: x for x in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_({x.child_part_no for x in bom_rows})).all()}
    grouped = {}
    for bom in bom_rows:
        key = bom.child_part_no
        if key not in grouped:
            grouped[key] = {"qty": 0.0, "unit": bom.unit or "EA", "sort": int(bom.sort_order or 1)}
        grouped[key]["qty"] += float(bom.quantity or 0)
    for part_no, data in grouped.items():
        db.add(ProductionRunMaterial(
            run_id=run.id,
            material_part_no=part_no,
            material_name=item_map.get(part_no).part_name if item_map.get(part_no) else "",
            unit=data["unit"],
            bom_qty=data["qty"],
            required_qty=0.0,
            sort_order=data["sort"],
        ))
    order.status = "IN_PROGRESS"
    db.commit()
    run = _get_run(db, run.id)
    run.work_order = order
    return _serialize_run(run)


@router.get("/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    run = _get_run(db, run_id)
    run.work_order = db.get(ProductionWorkOrder, run.work_order_id)
    return _serialize_run(run)


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

    run.defects.clear()
    defect_total = 0.0
    codes = [x.defect_type_code.strip() for x in payload.defects if x.defect_qty > 0]
    code_map = {}
    if codes:
        code_map = {x.code: x for x in db.query(CommonCodeModel).filter(CommonCodeModel.group_code == "DEFECT_TYPE", CommonCodeModel.code.in_(codes), CommonCodeModel.is_active == "Y").all()}
    for row in payload.defects:
        if row.defect_qty <= 0:
            continue
        code = row.defect_type_code.strip()
        master = code_map.get(code)
        if not master:
            raise HTTPException(400, f"사용할 수 없는 불량유형입니다: {code}")
        run.defects.append(ProductionRunDefect(defect_type_code=code, defect_type_name=master.code_name, defect_qty=row.defect_qty))
        defect_total += float(row.defect_qty)
    run.defect_qty = defect_total

    process_qty = run.good_qty + run.defect_qty + run.setup_qty
    for material in run.materials:
        material.required_qty = process_qty * float(material.bom_qty or 0)
        allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
        if allocated > material.required_qty + 1e-9:
            raise HTTPException(409, f"{material.material_part_no}의 기존 LOT 배정량이 새 필요수량보다 큽니다. LOT 배정을 초기화한 뒤 다시 진행하세요.")
    db.commit()
    run.work_order = db.get(ProductionWorkOrder, run.work_order_id)
    return _serialize_run(run)


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
        for lot in _lot_rows(db, material.material_part_no):
            if lot["lot_no"].upper() == scanned.upper():
                matched_material = material
                matched_lot = lot
                break
        if matched_material:
            break
    if not matched_material or not matched_lot:
        raise HTTPException(404, "이 작업의 BOM 자재에 해당하는 LOT가 아닙니다.")

    required = float(matched_material.required_qty or 0)
    allocated = sum(float(x.allocated_qty or 0) for x in matched_material.allocations)
    remaining = max(required - allocated, 0.0)
    if remaining <= 1e-9:
        raise HTTPException(409, f"{matched_material.material_part_no}는 이미 필요수량이 모두 배정되었습니다.")

    fifo_lot = None
    fifo_available = 0.0
    for lot in _lot_rows(db, matched_material.material_part_no):
        if any(x.lot_no == lot["lot_no"] for x in matched_material.allocations):
            continue
        available = _available_qty(db, lot["lot_no"], lot["base_qty"], current_run_id=run.id)
        if available > 1e-9:
            fifo_lot = lot
            fifo_available = available
            break
    if not fifo_lot:
        raise HTTPException(409, "선입선출 기준으로 배정 가능한 LOT 재고가 없습니다.")

    assign_qty = min(remaining, fifo_available)
    matched_material.allocations.append(ProductionRunLotAllocation(
        lot_no=fifo_lot["lot_no"],
        allocated_qty=assign_qty,
        source_type=fifo_lot["source_type"],
        storage_location=fifo_lot["storage_location"],
    ))
    db.commit()
    message = f"{fifo_lot['lot_no']}에 {assign_qty:g} 배정했습니다."
    if fifo_lot["lot_no"].upper() != scanned.upper():
        message = f"스캔 LOT {scanned}보다 선입 LOT {fifo_lot['lot_no']}를 우선 배정했습니다. ({assign_qty:g})"
    return {"message": message, "material": _serialize_material(matched_material)}


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
        raise HTTPException(400, "가공수량이 없습니다.")

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
        performance_type="MACHINING",
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
