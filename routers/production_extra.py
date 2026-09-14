from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.models import ItemMasterModel, ProcessModel
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_run import ProductionRun

router = APIRouter(prefix="/api/production", tags=["Production Extra"])


def _is_super_admin(user) -> bool:
    username = str(getattr(user, "username", "")).strip().lower()
    role = str(getattr(user, "role", "")).strip().upper()
    return username == "admin" or role == "SUPERADMIN"


@router.get("/performance-status")
def production_performance_status(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    performance_type: Optional[str] = Query(None),
    process_code: Optional[str] = Query(None),
    work_order_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    operator_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ProductionPerformance)
    if start_date:
        query = query.filter(ProductionPerformance.performance_date >= start_date)
    if end_date:
        query = query.filter(ProductionPerformance.performance_date <= end_date)
    if performance_type:
        value = performance_type.strip().upper()
        if value not in {"MACHINING", "ASSEMBLY"}:
            raise HTTPException(400, "지원하지 않는 생산실적 구분입니다.")
        query = query.filter(ProductionPerformance.performance_type == value)
    if process_code:
        query = query.filter(ProductionPerformance.process_code == process_code.strip())
    if operator_name:
        query = query.filter(ProductionPerformance.operator_name.contains(operator_name.strip(), autoescape=True))

    rows = query.order_by(ProductionPerformance.performance_date.desc(), ProductionPerformance.id.desc()).limit(2000).all()
    if not rows:
        return []

    order_ids = {x.work_order_id for x in rows}
    order_map = {x.id: x for x in db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id.in_(order_ids)).all()}

    if work_order_no:
        keyword = work_order_no.strip().lower()
        rows = [x for x in rows if x.work_order_id in order_map and keyword in order_map[x.work_order_id].work_order_no.lower()]
    if part_no:
        keyword = part_no.strip().lower()
        rows = [x for x in rows if x.work_order_id in order_map and keyword in order_map[x.work_order_id].part_no.lower()]

    part_nos = {order_map[x.work_order_id].part_no for x in rows if x.work_order_id in order_map}
    item_map = {
        x.part_no: x
        for x in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(part_nos)).all()
    } if part_nos else {}
    process_codes = {x.process_code for x in rows}
    process_map = {
        x.process_code: x
        for x in db.query(ProcessModel).filter(ProcessModel.process_code.in_(process_codes)).all()
    } if process_codes else {}

    result = []
    for perf in rows:
        order = order_map.get(perf.work_order_id)
        if not order:
            continue
        item = item_map.get(order.part_no)
        process = process_map.get(perf.process_code)
        result.append({
            "id": perf.id,
            "performance_date": perf.performance_date,
            "performance_type": perf.performance_type,
            "performance_type_name": "조립" if perf.performance_type == "ASSEMBLY" else "가공",
            "work_order_no": order.work_order_no,
            "part_no": order.part_no,
            "part_name": item.part_name if item else "",
            "process_code": perf.process_code,
            "process_name": process.process_name if process else "",
            "operator_name": perf.operator_name or "",
            "equipment_name": perf.equipment_name or perf.equipment_code or "",
            "shift_name": "주간" if perf.shift_type == "DAY" else ("야간" if perf.shift_type == "NIGHT" else ""),
            "good_qty": float(perf.good_qty or 0),
            "defect_qty": float(perf.defect_qty or 0),
            "setup_qty": float(perf.setup_qty or 0),
            "consumed_qty": float(perf.consumed_qty or 0),
            "source_lot_no": perf.source_lot_no or "",
            "note": perf.note or "",
        })
    return result


@router.delete("/orders/{order_id}/super-delete")
def super_delete_completed_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if not _is_super_admin(current_user):
        raise HTTPException(403, "최고관리자만 생산실적이 있는 작업지시를 삭제할 수 있습니다.")

    order = db.get(ProductionWorkOrder, order_id)
    if not order:
        raise HTTPException(404, "작업지시를 찾을 수 없습니다.")
    if order.status != "COMPLETED":
        raise HTTPException(400, "이 기능은 완료된 작업지시 삭제 전용입니다.")

    performance_ids = [x[0] for x in db.query(ProductionPerformance.id).filter(ProductionPerformance.work_order_id == order.id).all()]

    # LOT 소비원장을 먼저 삭제하면 해당 투입 LOT의 사용가능수량이 자동 복원됩니다.
    db.query(LotConsumptionModel).filter(LotConsumptionModel.work_order_id == order.id).delete(synchronize_session=False)

    # 가동내역의 자재배정/불량상세는 ORM cascade로 함께 삭제합니다.
    runs = db.query(ProductionRun).filter(ProductionRun.work_order_id == order.id).all()
    for run in runs:
        run.performance_id = None
        db.delete(run)
    db.flush()

    if performance_ids:
        db.query(ProductionPerformance).filter(ProductionPerformance.id.in_(performance_ids)).delete(synchronize_session=False)
    db.delete(order)
    db.commit()

    return {
        "status": "success",
        "message": "완료 작업지시와 연결 생산실적을 삭제했습니다. 투입 LOT 소비수량도 복원되었습니다.",
    }
