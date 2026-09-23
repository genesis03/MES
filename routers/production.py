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
from models.models import (
    ItemBomModel,
    ItemMasterModel,
    CommonCodeModel,
    ProcessModel,
    PurchaseInboundItem,
    PurchaseInboundMaster,
)
from models.production import ProductionPerformance, ProductionPlan, ProductionWorkOrder
from models.production_lot import ProductionLotModel
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.worker import WorkerMaster, WorkerProcess

router = APIRouter(prefix="/api/production", tags=["Production"])

STATUS_NAMES = {
    "WAITING": "대기",
    "IN_PROGRESS": "생산중",
    "COMPLETED": "완료",
    "CANCELLED": "취소",
}


class ProductionPlanPayload(BaseModel):
    plan_date: str
    part_no: str
    plan_qty: float = Field(gt=0)
    note: Optional[str] = None


class ProductionOrderPayload(BaseModel):
    order_date: str
    scheduled_date: Optional[str] = None
    plan_id: Optional[int] = None
    part_no: str
    order_qty: float = Field(gt=0)
    priority: int = Field(default=4, ge=1, le=4)
    note: Optional[str] = None


class ProductionOrderBatchItem(BaseModel):
    part_no: str
    order_qty: float = Field(gt=0)
    priority: int = Field(default=4, ge=1, le=4)


class ProductionOrderBatchPayload(BaseModel):
    order_date: str
    scheduled_date: Optional[str] = None
    items: List[ProductionOrderBatchItem]


class ProductionOrderStatusPayload(BaseModel):
    status: str


class ProductionPerformancePayload(BaseModel):
    work_order_id: int = Field(gt=0)
    performance_date: str
    process_code: str
    operator_id: int = Field(gt=0)
    equipment_id: int = Field(gt=0)
    shift_type: str
    source_lot_no: str
    good_qty: float = Field(gt=0)
    defect_qty: float = Field(ge=0, default=0)
    setup_qty: float = Field(ge=0, default=0)
    note: Optional[str] = None


def _user_name(user) -> str:
    return str(getattr(user, "name", None) or getattr(user, "username", None) or "")


def _item_or_404(db: Session, part_no: str):
    item = (
        db.query(ItemMasterModel)
        .filter(ItemMasterModel.part_no == part_no, ItemMasterModel.is_active == "Y")
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail=f"사용 가능한 품번을 찾을 수 없습니다. ({part_no})")
    return item


def _work_order_prefix_and_seq(db: Session, order_date: str):
    date_key = order_date.replace("-", "")[2:]
    prefix = f"W{date_key}"
    last = (
        db.query(ProductionWorkOrder)
        .filter(ProductionWorkOrder.work_order_no.like(f"{prefix}%"))
        .order_by(ProductionWorkOrder.work_order_no.desc())
        .first()
    )
    seq = 1
    if last and last.work_order_no[-3:].isdigit():
        seq = int(last.work_order_no[-3:]) + 1
    return prefix, seq


def _serialize_plan(plan: ProductionPlan, item: Optional[ItemMasterModel] = None):
    return {
        "id": plan.id,
        "plan_date": plan.plan_date,
        "item_id": plan.item_id,
        "part_no": item.part_no if item else plan.part_no,
        "part_name": item.part_name if item else "",
        "plan_qty": plan.plan_qty,
        "note": plan.note or "",
        "created_by": plan.created_by or "",
    }


def _serialize_order(order: ProductionWorkOrder, item: Optional[ItemMasterModel] = None):
    return {
        "id": order.id,
        "work_order_no": order.work_order_no,
        "order_date": order.order_date,
        "scheduled_date": order.scheduled_date or "",
        "plan_id": order.plan_id,
        "item_id": order.item_id,
        "part_no": item.part_no if item else order.part_no,
        "part_name": item.part_name if item else "",
        "order_qty": order.order_qty,
        "production_qty": order.production_qty,
        "progress_rate": round((order.production_qty / order.order_qty) * 100, 1) if order.order_qty else 0,
        "priority": int(order.priority or 4),
        "status": order.status,
        "status_name": STATUS_NAMES.get(order.status, order.status),
        "note": order.note or "",
        "created_by": order.created_by or "",
    }


def _serialize_performance(perf, order, item, process):
    return {
        "id": perf.id,
        "performance_date": perf.performance_date,
        "work_order_id": order.id,
        "work_order_no": order.work_order_no,
        "item_id": order.item_id,
        "part_no": item.part_no if item else order.part_no,
        "part_name": item.part_name if item else "",
        "process_code": perf.process_code,
        "process_name": process.process_name if process else "",
        "shift_type": perf.shift_type or "",
        "shift_name": "주간" if perf.shift_type == "DAY" else ("야간" if perf.shift_type == "NIGHT" else ""),
        "equipment_code": perf.equipment_code or "",
        "equipment_name": perf.equipment_name or "",
        "source_lot_no": perf.source_lot_no or "",
        "good_qty": perf.good_qty,
        "defect_qty": perf.defect_qty,
        "setup_qty": perf.setup_qty or 0,
        "consumed_qty": perf.consumed_qty or 0,
        "operator_name": perf.operator_name or "",
        "note": perf.note or "",
        "created_by": perf.created_by or "",
    }


def _lot_base_rows(db: Session, item_ids: List[int]):
    result = []
    item_ids = [int(x) for x in item_ids if x]
    if not item_ids:
        return result

    item_map = {
        item.id: item
        for item in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all()
    }
    purchase_rows = (
        db.query(PurchaseInboundItem)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.item_id.in_(item_ids),
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .all()
    )
    for row in purchase_rows:
        item = item_map.get(row.item_id)
        result.append({
            "lot_no": row.internal_lot_no,
            "item_id": row.item_id,
            "part_no": item.part_no if item else row.part_no,
            "base_qty": float(row.inbound_qty or 0),
            "storage_location": row.storage_location or "",
            "source": "PURCHASE",
        })

    production_rows = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.item_id.in_(item_ids), ProductionLotModel.status == "ACTIVE")
        .all()
    )
    for row in production_rows:
        item = item_map.get(row.item_id)
        result.append({
            "lot_no": row.lot_no,
            "item_id": row.item_id,
            "part_no": item.part_no if item else row.part_no,
            "base_qty": float(row.lot_qty or 0),
            "storage_location": row.storage_location or "",
            "source": "PRODUCTION",
        })
    return result


def _lot_available_qty(db: Session, lot_no: str, base_qty: float) -> float:
    linked_consumed = (
        db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0))
        .filter(LotRelationModel.parent_lot_no == lot_no)
        .scalar()
        or 0.0
    )
    production_consumed = (
        db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0))
        .filter(LotConsumptionModel.lot_no == lot_no)
        .scalar()
        or 0.0
    )
    reserved = (
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
    return max(float(base_qty) - float(linked_consumed) - float(production_consumed) - float(reserved), 0.0)


def _previous_item_ids(db: Session, output_item_id: int, process_code: str) -> List[int]:
    rows = (
        db.query(ItemBomModel)
        .filter(
            ItemBomModel.parent_item_id == output_item_id,
            ItemBomModel.process_code == process_code,
        )
        .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
        .all()
    )
    if not rows:
        rows = (
            db.query(ItemBomModel)
            .filter(ItemBomModel.parent_item_id == output_item_id)
            .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
            .all()
        )
    return list(dict.fromkeys(int(row.child_item_id) for row in rows if row.child_item_id))


@router.get("/items")
def production_items(keyword: Optional[str] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    if keyword:
        value = f"%{keyword.strip()}%"
        query = query.filter((ItemMasterModel.part_no.ilike(value)) | (ItemMasterModel.part_name.ilike(value)))
    items = query.order_by(ItemMasterModel.part_no.asc()).limit(1000).all()
    return [{"item_id": item.id, "part_no": item.part_no, "part_name": item.part_name, "unit": item.unit or "EA"} for item in items]


@router.get("/order-items/search")
def search_order_items(part_no: Optional[str] = Query(None), part_name: Optional[str] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    if part_no:
        query = query.filter(ItemMasterModel.part_no.ilike(f"%{part_no.strip()}%"))
    if part_name:
        query = query.filter(ItemMasterModel.part_name.ilike(f"%{part_name.strip()}%"))
    items = query.order_by(ItemMasterModel.part_no.asc()).limit(500).all()
    if not items:
        return []

    item_ids = [x.id for x in items]
    bom_rows = db.query(ItemBomModel).filter(ItemBomModel.child_item_id.in_(item_ids)).order_by(ItemBomModel.child_item_id.asc(), ItemBomModel.sort_order.asc(), ItemBomModel.id.asc()).all()
    bom_map = {}
    for row in bom_rows:
        if row.child_item_id not in bom_map:
            bom_map[row.child_item_id] = row
    parent_ids = {row.parent_item_id for row in bom_rows if row.parent_item_id}
    parent_map = {x.id: x for x in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(parent_ids)).all()} if parent_ids else {}
    process_codes = {(bom_map.get(item.id).process_code if bom_map.get(item.id) else item.production_loc) for item in items}
    process_codes.discard(None); process_codes.discard("")
    process_map = {x.process_code: x for x in db.query(ProcessModel).filter(ProcessModel.process_code.in_(process_codes)).all()} if process_codes else {}
    material_type_codes = {str(item.material_type or "").strip() for item in items if str(item.material_type or "").strip()}
    material_type_map = {
        row.code: row.code_name
        for row in db.query(CommonCodeModel)
        .filter(
            CommonCodeModel.group_code == "MATERIAL_TYPE",
            CommonCodeModel.code.in_(material_type_codes),
            CommonCodeModel.is_active == "Y",
        )
        .all()
    } if material_type_codes else {}
    production_rows = db.query(ProductionWorkOrder.item_id, func.coalesce(func.sum(ProductionPerformance.good_qty), 0)).join(ProductionPerformance, ProductionPerformance.work_order_id == ProductionWorkOrder.id).filter(ProductionWorkOrder.item_id.in_(item_ids)).group_by(ProductionWorkOrder.item_id).all()
    production_map = {item_id: float(qty or 0) for item_id, qty in production_rows}
    stock_rows = db.query(ProductionLotModel.item_id, func.coalesce(func.sum(ProductionLotModel.lot_qty), 0)).filter(ProductionLotModel.item_id.in_(item_ids), ProductionLotModel.status == "ACTIVE").group_by(ProductionLotModel.item_id).all()
    stock_map = {item_id: float(qty or 0) for item_id, qty in stock_rows}
    result = []
    for item in items:
        bom = bom_map.get(item.id)
        process_code = (bom.process_code if bom else None) or item.production_loc or ""
        process = process_map.get(process_code)
        parent = parent_map.get(bom.parent_item_id) if bom else None
        result.append({"item_id": item.id, "part_no": item.part_no, "part_name": item.part_name, "material_type": material_type_map.get(item.material_type, item.material_type or ""), "parent_part_no": parent.part_no if parent else "", "process_code": process_code, "process_name": process.process_name if process else "", "process_order": int(bom.sort_order or 0) if bom else 0, "safety_stock": float(item.safety_stock or 0), "current_stock": stock_map.get(item.id, 0), "production_qty": production_map.get(item.id, 0)})
    return result


@router.get("/processes")
def production_processes(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = db.query(ProcessModel).filter(ProcessModel.is_active == "Y").order_by(ProcessModel.sort_order.asc(), ProcessModel.process_code.asc()).all()
    return [{"process_code": x.process_code, "process_name": x.process_name} for x in rows]


@router.get("/workers")
def production_workers(process_code: str = Query(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    code = process_code.strip()
    rows = db.query(WorkerMaster).join(WorkerProcess, WorkerProcess.worker_id == WorkerMaster.id).filter(WorkerMaster.is_active == "Y", WorkerProcess.process_code == code).order_by(WorkerMaster.worker_code.asc()).all()
    return [{"id": x.id, "worker_code": x.worker_code, "worker_name": x.worker_name, "department": x.department or ""} for x in rows]


@router.get("/equipments")
def production_equipments(process_code: str = Query(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    code = process_code.strip()
    rows = db.query(EquipmentMaster).filter(EquipmentMaster.is_active == "Y", EquipmentMaster.process_code == code).order_by(EquipmentMaster.sort_order.asc(), EquipmentMaster.machine_no.asc()).all()
    return [{"id": x.id, "equipment_code": x.equipment_code, "equipment_name": x.equipment_name, "machine_no": x.machine_no} for x in rows]


@router.get("/performance/source-lots")
def performance_source_lots(work_order_id: int = Query(..., gt=0), process_code: str = Query(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order = db.get(ProductionWorkOrder, work_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")
    previous_item_ids = _previous_item_ids(db, order.item_id, process_code.strip())
    if not previous_item_ids:
        return {"previous_parts": [], "items": [], "message": "BOM에서 이전 품번을 찾을 수 없습니다."}
    previous_parts = [
        row.part_no
        for row in db.query(ItemMasterModel)
        .filter(ItemMasterModel.id.in_(previous_item_ids))
        .order_by(ItemMasterModel.part_no.asc())
        .all()
    ]
    items = []
    for row in _lot_base_rows(db, previous_item_ids):
        available = _lot_available_qty(db, row["lot_no"], row["base_qty"])
        if available > 0:
            items.append({**row, "available_qty": available})
    items.sort(key=lambda x: (x["part_no"], x["lot_no"]))
    return {"previous_parts": previous_parts, "items": items}


@router.get("/plans")
def list_plans(start_date: Optional[str] = Query(None), end_date: Optional[str] = Query(None), part_no: Optional[str] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(ProductionPlan)
    if start_date: query = query.filter(ProductionPlan.plan_date >= start_date)
    if end_date: query = query.filter(ProductionPlan.plan_date <= end_date)
    if part_no: query = query.filter(ProductionPlan.part_no.ilike(f"%{part_no.strip()}%"))
    plans = query.order_by(ProductionPlan.plan_date.desc(), ProductionPlan.id.desc()).all()
    item_ids = {p.item_id for p in plans if p.item_id}
    item_map = {item.id: item for item in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all()} if item_ids else {}
    return [_serialize_plan(plan, item_map.get(plan.item_id)) for plan in plans]


@router.post("/plans")
def create_plan(payload: ProductionPlanPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    part_no = payload.part_no.strip(); item = _item_or_404(db, part_no)
    plan = ProductionPlan(plan_date=payload.plan_date, item_id=item.id, part_no=item.part_no, plan_qty=payload.plan_qty, note=(payload.note or "").strip() or None, created_by=_user_name(current_user))
    db.add(plan); db.commit(); db.refresh(plan)
    return {"status": "success", "id": plan.id, "message": "생산계획이 등록되었습니다."}


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, payload: ProductionPlanPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    plan = db.get(ProductionPlan, plan_id)
    if not plan: raise HTTPException(status_code=404, detail="생산계획을 찾을 수 없습니다.")
    part_no = payload.part_no.strip(); item = _item_or_404(db, part_no)
    plan.plan_date = payload.plan_date; plan.item_id = item.id; plan.part_no = item.part_no; plan.plan_qty = payload.plan_qty; plan.note = (payload.note or "").strip() or None
    db.commit(); return {"status": "success", "message": "생산계획이 수정되었습니다."}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    plan = db.get(ProductionPlan, plan_id)
    if not plan: raise HTTPException(status_code=404, detail="생산계획을 찾을 수 없습니다.")
    if db.query(ProductionWorkOrder.id).filter(ProductionWorkOrder.plan_id == plan_id).first(): raise HTTPException(status_code=400, detail="작업지시에 연결된 생산계획은 삭제할 수 없습니다.")
    db.delete(plan); db.commit(); return {"status": "success", "message": "생산계획이 삭제되었습니다."}


@router.get("/orders")
def list_orders(start_date: Optional[str] = Query(None), end_date: Optional[str] = Query(None), part_no: Optional[str] = Query(None), status: Optional[str] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(ProductionWorkOrder)
    if start_date: query = query.filter(ProductionWorkOrder.order_date >= start_date)
    if end_date: query = query.filter(ProductionWorkOrder.order_date <= end_date)
    if part_no: query = query.filter(ProductionWorkOrder.part_no.ilike(f"%{part_no.strip()}%"))
    if status: query = query.filter(ProductionWorkOrder.status == status)
    orders = query.order_by(ProductionWorkOrder.order_date.desc(), ProductionWorkOrder.priority.asc(), ProductionWorkOrder.id.desc()).all()
    item_ids = {o.item_id for o in orders if o.item_id}
    item_map = {item.id: item for item in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all()} if item_ids else {}
    return [_serialize_order(order, item_map.get(order.item_id)) for order in orders]


@router.get("/performance/orders")
def performance_orders(process_code: Optional[str] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    orders = db.query(ProductionWorkOrder).filter(ProductionWorkOrder.status.in_(["WAITING", "IN_PROGRESS"])).order_by(ProductionWorkOrder.priority.asc(), ProductionWorkOrder.order_date.desc(), ProductionWorkOrder.id.desc()).all()
    if process_code:
        code = process_code.strip()
        bom_items = {x[0] for x in db.query(ItemBomModel.parent_item_id).filter(ItemBomModel.process_code == code).distinct().all() if x[0]}
        item_ids_for_process = {x[0] for x in db.query(ItemMasterModel.id).filter(ItemMasterModel.production_loc == code).all()}
        allowed = bom_items | item_ids_for_process
        orders = [x for x in orders if x.item_id in allowed]
    item_ids = {o.item_id for o in orders if o.item_id}
    item_map = {item.id: item for item in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all()} if item_ids else {}
    return [_serialize_order(order, item_map.get(order.item_id)) for order in orders]


@router.post("/orders")
def create_order(payload: ProductionOrderPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    part_no = payload.part_no.strip(); item = _item_or_404(db, part_no)
    if payload.plan_id is not None:
        plan = db.get(ProductionPlan, payload.plan_id)
        if not plan: raise HTTPException(status_code=404, detail="연결할 생산계획을 찾을 수 없습니다.")
        if plan.item_id != item.id: raise HTTPException(status_code=400, detail="생산계획 품목과 작업지시 품목이 일치하지 않습니다.")
    prefix, seq = _work_order_prefix_and_seq(db, payload.order_date)
    order = ProductionWorkOrder(work_order_no=f"{prefix}{seq:03d}", order_date=payload.order_date, scheduled_date=(payload.scheduled_date or "").strip() or None, plan_id=payload.plan_id, item_id=item.id, part_no=item.part_no, order_qty=payload.order_qty, production_qty=0, priority=payload.priority, status="WAITING", note=(payload.note or "").strip() or None, created_by=_user_name(current_user))
    db.add(order); db.commit(); db.refresh(order)
    return {"status": "success", "id": order.id, "work_order_no": order.work_order_no, "message": "작업지시가 등록되었습니다."}


@router.post("/orders/batch")
def create_orders_batch(payload: ProductionOrderBatchPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if not payload.order_date.strip(): raise HTTPException(status_code=400, detail="지시일자는 필수입니다.")
    if not payload.items: raise HTTPException(status_code=400, detail="작업지시 대상 품번을 선택해 주세요.")
    prefix, seq = _work_order_prefix_and_seq(db, payload.order_date); created = []
    for index, row in enumerate(payload.items):
        part_no = row.part_no.strip(); item = _item_or_404(db, part_no)
        order = ProductionWorkOrder(work_order_no=f"{prefix}{seq + index:03d}", order_date=payload.order_date, scheduled_date=(payload.scheduled_date or "").strip() or None, plan_id=None, item_id=item.id, part_no=item.part_no, order_qty=row.order_qty, production_qty=0, priority=row.priority, status="WAITING", created_by=_user_name(current_user))
        db.add(order); created.append(order)
    db.commit(); return {"status": "success", "count": len(created), "work_order_nos": [x.work_order_no for x in created], "message": f"작업지시 {len(created)}건이 생성되었습니다."}


@router.patch("/orders/{order_id}/status")
def update_order_status(order_id: int, payload: ProductionOrderStatusPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order = db.get(ProductionWorkOrder, order_id)
    if not order: raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")
    status = payload.status.strip().upper()
    if status not in {"COMPLETED", "CANCELLED"}: raise HTTPException(status_code=400, detail="완료 또는 취소 버튼으로만 상태를 변경할 수 있습니다.")
    if order.status in {"COMPLETED", "CANCELLED"}: raise HTTPException(status_code=400, detail="이미 종료된 작업지시입니다.")
    if status == "COMPLETED" and order.production_qty <= 0: raise HTTPException(status_code=400, detail="생산실적이 없는 작업지시는 완료 처리할 수 없습니다.")
    if status == "CANCELLED" and order.production_qty > 0: raise HTTPException(status_code=400, detail="생산실적이 존재하는 작업지시는 취소할 수 없습니다.")
    order.status = status; db.commit(); return {"status": "success", "message": "작업지시 상태가 변경되었습니다."}


@router.get("/performances")
def list_performances(start_date: Optional[str] = Query(None), end_date: Optional[str] = Query(None), work_order_no: Optional[str] = Query(None), part_no: Optional[str] = Query(None), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    query = db.query(ProductionPerformance).filter(ProductionPerformance.performance_type == "MACHINING")
    if start_date: query = query.filter(ProductionPerformance.performance_date >= start_date)
    if end_date: query = query.filter(ProductionPerformance.performance_date <= end_date)
    rows = query.order_by(ProductionPerformance.performance_date.desc(), ProductionPerformance.id.desc()).all()
    if not rows: return []
    order_map = {x.id: x for x in db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id.in_({x.work_order_id for x in rows})).all()}
    if work_order_no:
        value = work_order_no.strip().lower(); rows = [x for x in rows if value in order_map[x.work_order_id].work_order_no.lower()]
    if part_no:
        value = part_no.strip().lower(); rows = [x for x in rows if value in order_map[x.work_order_id].part_no.lower()]
    item_ids = {order_map[x.work_order_id].item_id for x in rows if order_map[x.work_order_id].item_id}
    item_map = {x.id: x for x in db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all()} if item_ids else {}
    process_codes = {x.process_code for x in rows}
    process_map = {x.process_code: x for x in db.query(ProcessModel).filter(ProcessModel.process_code.in_(process_codes)).all()} if process_codes else {}
    return [_serialize_performance(x, order_map[x.work_order_id], item_map.get(order_map[x.work_order_id].item_id), process_map.get(x.process_code)) for x in rows]


@router.post("/performances")
def create_performance(payload: ProductionPerformancePayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order = db.get(ProductionWorkOrder, payload.work_order_id)
    if not order: raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")
    if order.status in {"COMPLETED", "CANCELLED"}: raise HTTPException(status_code=400, detail="완료 또는 취소된 작업지시에는 생산실적을 등록할 수 없습니다.")

    process_code = payload.process_code.strip()
    process = db.query(ProcessModel).filter(ProcessModel.process_code == process_code, ProcessModel.is_active == "Y").first()
    if not process: raise HTTPException(status_code=404, detail="사용 가능한 공정을 찾을 수 없습니다.")

    worker = db.query(WorkerMaster).join(WorkerProcess, WorkerProcess.worker_id == WorkerMaster.id).filter(WorkerMaster.id == payload.operator_id, WorkerMaster.is_active == "Y", WorkerProcess.process_code == process_code).first()
    if not worker: raise HTTPException(status_code=400, detail="선택한 작업자는 해당 공정에 등록할 수 없습니다.")

    equipment = db.query(EquipmentMaster).filter(EquipmentMaster.id == payload.equipment_id, EquipmentMaster.is_active == "Y", EquipmentMaster.process_code == process_code).first()
    if not equipment: raise HTTPException(status_code=400, detail="선택한 생산 호기는 해당 공정에 등록할 수 없습니다.")

    shift_type = payload.shift_type.strip().upper()
    if shift_type not in {"DAY", "NIGHT"}: raise HTTPException(status_code=400, detail="주간 또는 야간을 선택해 주세요.")

    source_lot_no = payload.source_lot_no.strip()
    previous_item_ids = _previous_item_ids(db, order.item_id, process_code)
    if not previous_item_ids: raise HTTPException(status_code=400, detail="BOM에서 해당 작업지시의 이전 품번을 찾을 수 없습니다.")
    source_rows = {row["lot_no"]: row for row in _lot_base_rows(db, previous_item_ids)}
    source = source_rows.get(source_lot_no)
    if not source: raise HTTPException(status_code=400, detail="해당 공정에서 사용할 수 있는 이전 LOT가 아닙니다.")

    consumed_qty = float(payload.good_qty) + float(payload.defect_qty) + float(payload.setup_qty)
    available_qty = _lot_available_qty(db, source_lot_no, source["base_qty"])
    if consumed_qty > available_qty + 1e-9:
        raise HTTPException(status_code=400, detail=f"LOT 사용가능수량이 부족합니다. 사용가능 {available_qty:g}, 필요 {consumed_qty:g}")

    perf = ProductionPerformance(
        work_order_id=order.id,
        performance_type="MACHINING",
        performance_date=payload.performance_date,
        process_code=process_code,
        shift_type=shift_type,
        operator_id=worker.id,
        operator_name=worker.worker_name,
        equipment_id=equipment.id,
        equipment_code=equipment.equipment_code,
        equipment_name=equipment.equipment_name,
        source_lot_no=source_lot_no,
        good_qty=payload.good_qty,
        defect_qty=payload.defect_qty,
        setup_qty=payload.setup_qty,
        consumed_qty=consumed_qty,
        note=(payload.note or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(perf); db.flush()
    db.add(LotConsumptionModel(lot_no=source_lot_no, item_id=source["item_id"], part_no=source["part_no"], work_order_id=order.id, performance_id=perf.id, process_code=process_code, consumed_qty=consumed_qty))

    # 작업지시 생산누계는 양품만 반영합니다. 불량/SET-UP은 원재료 LOT 소비에만 포함됩니다.
    order.production_qty = float(order.production_qty or 0) + float(payload.good_qty)
    if order.status == "WAITING": order.status = "IN_PROGRESS"
    db.commit(); db.refresh(perf)

    over_qty = max(float(order.production_qty or 0) - float(order.order_qty or 0), 0)
    remaining_lot_qty = max(available_qty - consumed_qty, 0)
    return {"status": "success", "id": perf.id, "work_order_status": order.status, "production_qty": order.production_qty, "consumed_qty": consumed_qty, "remaining_lot_qty": remaining_lot_qty, "over_qty": over_qty, "message": "생산실적이 등록되었습니다." if over_qty <= 0 else f"생산실적이 등록되었습니다. 지시수량을 {over_qty:g} 초과했습니다."}


@router.delete("/orders/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order = db.get(ProductionWorkOrder, order_id)
    if not order: raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")
    if order.production_qty > 0 or order.status not in {"WAITING", "CANCELLED"}: raise HTTPException(status_code=400, detail="생산이 시작된 작업지시는 삭제할 수 없습니다.")
    db.delete(order); db.commit(); return {"status": "success", "message": "작업지시가 삭제되었습니다."}
