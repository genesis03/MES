from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel, ProcessModel
from models.production import ProductionPerformance, ProductionPlan, ProductionWorkOrder
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
    note: Optional[str] = None


class ProductionOrderStatusPayload(BaseModel):
    status: str


class ProductionPerformancePayload(BaseModel):
    work_order_id: int
    performance_date: str
    process_code: str
    good_qty: float = Field(gt=0)
    defect_qty: float = Field(ge=0, default=0)
    operator_id: Optional[int] = None
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


def _serialize_plan(plan: ProductionPlan, item: Optional[ItemMasterModel] = None):
    return {
        "id": plan.id,
        "plan_date": plan.plan_date,
        "part_no": plan.part_no,
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
        "part_no": order.part_no,
        "part_name": item.part_name if item else "",
        "order_qty": order.order_qty,
        "production_qty": order.production_qty,
        "progress_rate": round((order.production_qty / order.order_qty) * 100, 1) if order.order_qty else 0,
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
        "part_no": order.part_no,
        "part_name": item.part_name if item else "",
        "process_code": perf.process_code,
        "process_name": process.process_name if process else "",
        "good_qty": perf.good_qty,
        "defect_qty": perf.defect_qty,
        "operator_name": perf.operator_name or "",
        "note": perf.note or "",
        "created_by": perf.created_by or "",
    }


@router.get("/items")
def production_items(
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    if keyword:
        value = f"%{keyword.strip()}%"
        query = query.filter(
            (ItemMasterModel.part_no.ilike(value)) | (ItemMasterModel.part_name.ilike(value))
        )
    items = query.order_by(ItemMasterModel.part_no.asc()).limit(1000).all()
    return [
        {"part_no": item.part_no, "part_name": item.part_name, "unit": item.unit or "EA"}
        for item in items
    ]


@router.get("/processes")
def production_processes(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    rows = (
        db.query(ProcessModel)
        .filter(ProcessModel.is_active == "Y")
        .order_by(ProcessModel.sort_order.asc(), ProcessModel.process_code.asc())
        .all()
    )
    return [{"process_code": x.process_code, "process_name": x.process_name} for x in rows]


@router.get("/workers")
def production_workers(
    process_code: str = Query(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    code = process_code.strip()
    rows = (
        db.query(WorkerMaster)
        .join(WorkerProcess, WorkerProcess.worker_id == WorkerMaster.id)
        .filter(
            WorkerMaster.is_active == "Y",
            WorkerProcess.process_code == code,
        )
        .order_by(WorkerMaster.worker_code.asc())
        .all()
    )
    return [
        {
            "id": x.id,
            "worker_code": x.worker_code,
            "worker_name": x.worker_name,
            "department": x.department or "",
        }
        for x in rows
    ]


@router.get("/plans")
def list_plans(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ProductionPlan)
    if start_date:
        query = query.filter(ProductionPlan.plan_date >= start_date)
    if end_date:
        query = query.filter(ProductionPlan.plan_date <= end_date)
    if part_no:
        query = query.filter(ProductionPlan.part_no.ilike(f"%{part_no.strip()}%"))

    plans = query.order_by(ProductionPlan.plan_date.desc(), ProductionPlan.id.desc()).all()
    item_map = {
        item.part_no: item
        for item in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({p.part_no for p in plans})
        ).all()
    } if plans else {}
    return [_serialize_plan(plan, item_map.get(plan.part_no)) for plan in plans]


@router.post("/plans")
def create_plan(
    payload: ProductionPlanPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part_no = payload.part_no.strip()
    _item_or_404(db, part_no)
    plan = ProductionPlan(
        plan_date=payload.plan_date,
        part_no=part_no,
        plan_qty=payload.plan_qty,
        note=(payload.note or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return {"status": "success", "id": plan.id, "message": "생산계획이 등록되었습니다."}


@router.put("/plans/{plan_id}")
def update_plan(
    plan_id: int,
    payload: ProductionPlanPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plan = db.query(ProductionPlan).filter(ProductionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="생산계획을 찾을 수 없습니다.")
    part_no = payload.part_no.strip()
    _item_or_404(db, part_no)
    plan.plan_date = payload.plan_date
    plan.part_no = part_no
    plan.plan_qty = payload.plan_qty
    plan.note = (payload.note or "").strip() or None
    db.commit()
    return {"status": "success", "message": "생산계획이 수정되었습니다."}


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    plan = db.query(ProductionPlan).filter(ProductionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="생산계획을 찾을 수 없습니다.")
    linked = db.query(ProductionWorkOrder.id).filter(ProductionWorkOrder.plan_id == plan_id).first()
    if linked:
        raise HTTPException(status_code=400, detail="작업지시에 연결된 생산계획은 삭제할 수 없습니다.")
    db.delete(plan)
    db.commit()
    return {"status": "success", "message": "생산계획이 삭제되었습니다."}


@router.get("/orders")
def list_orders(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ProductionWorkOrder)
    if start_date:
        query = query.filter(ProductionWorkOrder.order_date >= start_date)
    if end_date:
        query = query.filter(ProductionWorkOrder.order_date <= end_date)
    if part_no:
        query = query.filter(ProductionWorkOrder.part_no.ilike(f"%{part_no.strip()}%"))
    if status:
        query = query.filter(ProductionWorkOrder.status == status)

    orders = query.order_by(ProductionWorkOrder.order_date.desc(), ProductionWorkOrder.id.desc()).all()
    item_map = {
        item.part_no: item
        for item in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({o.part_no for o in orders})
        ).all()
    } if orders else {}
    return [_serialize_order(order, item_map.get(order.part_no)) for order in orders]


@router.get("/performance/orders")
def performance_orders(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    orders = (
        db.query(ProductionWorkOrder)
        .filter(ProductionWorkOrder.status.in_(["WAITING", "IN_PROGRESS"]))
        .order_by(ProductionWorkOrder.order_date.desc(), ProductionWorkOrder.id.desc())
        .all()
    )
    item_map = {
        item.part_no: item
        for item in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({o.part_no for o in orders})
        ).all()
    } if orders else {}
    return [_serialize_order(order, item_map.get(order.part_no)) for order in orders]


@router.post("/orders")
def create_order(
    payload: ProductionOrderPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part_no = payload.part_no.strip()
    _item_or_404(db, part_no)

    if payload.plan_id is not None:
        plan = db.query(ProductionPlan).filter(ProductionPlan.id == payload.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="연결할 생산계획을 찾을 수 없습니다.")
        if plan.part_no != part_no:
            raise HTTPException(status_code=400, detail="생산계획 품번과 작업지시 품번이 일치하지 않습니다.")

    date_key = payload.order_date.replace("-", "")[2:]
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
    work_order_no = f"{prefix}{seq:03d}"

    order = ProductionWorkOrder(
        work_order_no=work_order_no,
        order_date=payload.order_date,
        scheduled_date=(payload.scheduled_date or "").strip() or None,
        plan_id=payload.plan_id,
        part_no=part_no,
        order_qty=payload.order_qty,
        production_qty=0,
        status="WAITING",
        note=(payload.note or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return {
        "status": "success",
        "id": order.id,
        "work_order_no": order.work_order_no,
        "message": "작업지시가 등록되었습니다.",
    }


@router.patch("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    payload: ProductionOrderStatusPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")

    status = payload.status.strip().upper()
    if status not in {"COMPLETED", "CANCELLED"}:
        raise HTTPException(status_code=400, detail="완료 또는 취소 버튼으로만 상태를 변경할 수 있습니다.")
    if order.status in {"COMPLETED", "CANCELLED"}:
        raise HTTPException(status_code=400, detail="이미 종료된 작업지시입니다.")
    if status == "COMPLETED" and order.production_qty <= 0:
        raise HTTPException(status_code=400, detail="생산실적이 없는 작업지시는 완료 처리할 수 없습니다.")
    if status == "CANCELLED" and order.production_qty > 0:
        raise HTTPException(status_code=400, detail="생산실적이 존재하는 작업지시는 취소할 수 없습니다.")

    order.status = status
    db.commit()
    return {"status": "success", "message": "작업지시 상태가 변경되었습니다."}


@router.get("/performances")
def list_performances(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    work_order_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ProductionPerformance).filter(ProductionPerformance.performance_type == "MACHINING")
    if start_date:
        query = query.filter(ProductionPerformance.performance_date >= start_date)
    if end_date:
        query = query.filter(ProductionPerformance.performance_date <= end_date)

    rows = query.order_by(ProductionPerformance.performance_date.desc(), ProductionPerformance.id.desc()).all()
    if not rows:
        return []

    order_ids = {x.work_order_id for x in rows}
    orders = db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id.in_(order_ids)).all()
    order_map = {x.id: x for x in orders}
    if work_order_no:
        value = work_order_no.strip().lower()
        rows = [x for x in rows if value in order_map[x.work_order_id].work_order_no.lower()]
    if part_no:
        value = part_no.strip().lower()
        rows = [x for x in rows if value in order_map[x.work_order_id].part_no.lower()]

    part_nos = {order_map[x.work_order_id].part_no for x in rows}
    item_map = {
        x.part_no: x for x in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(part_nos)).all()
    } if part_nos else {}
    process_codes = {x.process_code for x in rows}
    process_map = {
        x.process_code: x for x in db.query(ProcessModel).filter(ProcessModel.process_code.in_(process_codes)).all()
    } if process_codes else {}

    return [
        _serialize_performance(
            x,
            order_map[x.work_order_id],
            item_map.get(order_map[x.work_order_id].part_no),
            process_map.get(x.process_code),
        )
        for x in rows
    ]


@router.post("/performances")
def create_performance(
    payload: ProductionPerformancePayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id == payload.work_order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")
    if order.status in {"COMPLETED", "CANCELLED"}:
        raise HTTPException(status_code=400, detail="완료 또는 취소된 작업지시에는 생산실적을 등록할 수 없습니다.")

    process_code = payload.process_code.strip()
    process = (
        db.query(ProcessModel)
        .filter(ProcessModel.process_code == process_code, ProcessModel.is_active == "Y")
        .first()
    )
    if not process:
        raise HTTPException(status_code=404, detail="사용 가능한 공정을 찾을 수 없습니다.")

    operator_name = None
    if payload.operator_id is not None:
        worker = (
            db.query(WorkerMaster)
            .join(WorkerProcess, WorkerProcess.worker_id == WorkerMaster.id)
            .filter(
                WorkerMaster.id == payload.operator_id,
                WorkerMaster.is_active == "Y",
                WorkerProcess.process_code == process_code,
            )
            .first()
        )
        if not worker:
            raise HTTPException(status_code=400, detail="선택한 작업자는 해당 공정에 등록할 수 없습니다.")
        operator_name = worker.worker_name

    perf = ProductionPerformance(
        work_order_id=order.id,
        performance_type="MACHINING",
        performance_date=payload.performance_date,
        process_code=process_code,
        good_qty=payload.good_qty,
        defect_qty=payload.defect_qty,
        operator_name=operator_name,
        note=(payload.note or "").strip() or None,
        created_by=_user_name(current_user),
    )
    db.add(perf)
    order.production_qty = float(order.production_qty or 0) + payload.good_qty
    if order.status == "WAITING":
        order.status = "IN_PROGRESS"
    db.commit()
    db.refresh(perf)

    over_qty = max(float(order.production_qty or 0) - float(order.order_qty or 0), 0)
    return {
        "status": "success",
        "id": perf.id,
        "work_order_status": order.status,
        "production_qty": order.production_qty,
        "over_qty": over_qty,
        "message": "생산실적이 등록되었습니다." if over_qty <= 0 else f"생산실적이 등록되었습니다. 지시수량을 {over_qty:g} 초과했습니다.",
    }


@router.delete("/orders/{order_id}")
def delete_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="작업지시를 찾을 수 없습니다.")
    if order.production_qty > 0 or order.status not in {"WAITING", "CANCELLED"}:
        raise HTTPException(status_code=400, detail="생산이 시작된 작업지시는 삭제할 수 없습니다.")
    db.delete(order)
    db.commit()
    return {"status": "success", "message": "작업지시가 삭제되었습니다."}
