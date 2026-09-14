from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel
from models.production import ProductionPlan, ProductionWorkOrder

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


@router.post("/orders")
def create_order(
    payload: ProductionOrderPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part_no = payload.part_no.strip()
    _item_or_404(db, part_no)

    plan = None
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
    if status not in STATUS_NAMES:
        raise HTTPException(status_code=400, detail="사용할 수 없는 작업지시 상태입니다.")
    if status == "IN_PROGRESS" and order.status != "IN_PROGRESS":
        raise HTTPException(status_code=400, detail="생산중 상태는 첫 생산실적 등록 시 자동으로 변경됩니다.")
    if status == "WAITING" and order.status != "WAITING":
        raise HTTPException(status_code=400, detail="생산이 시작된 작업지시는 대기 상태로 되돌릴 수 없습니다.")
    if status == "CANCELLED" and order.production_qty > 0:
        raise HTTPException(status_code=400, detail="생산실적이 존재하는 작업지시는 취소할 수 없습니다.")
    order.status = status
    db.commit()
    return {"status": "success", "message": "작업지시 상태가 변경되었습니다."}


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
