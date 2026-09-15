from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.production import ProductionWorkOrder
from models.production_run import ProductionRun

router = APIRouter(prefix="/api/production-run", tags=["Production Run Delete"])


@router.delete("/{run_id}")
def delete_in_progress_run(
    run_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise HTTPException(404, "가동내역을 찾을 수 없습니다.")
    if run.status != "IN_PROGRESS" or run.performance_id is not None:
        raise HTTPException(409, "생산중 가동내역만 삭제할 수 있습니다.")

    order = db.get(ProductionWorkOrder, run.work_order_id)
    db.delete(run)
    db.flush()

    if order is not None:
        production_qty = float(order.production_qty or 0)
        order_qty = float(order.order_qty or 0)
        other_active = (
            db.query(ProductionRun.id)
            .filter(
                ProductionRun.work_order_id == order.id,
                ProductionRun.status == "IN_PROGRESS",
            )
            .first()
        )
        if order_qty > 0 and production_qty >= order_qty:
            order.status = "COMPLETED"
        elif other_active or production_qty > 0:
            order.status = "IN_PROGRESS"
        else:
            order.status = "WAITING"

    db.commit()
    return {
        "status": "success",
        "message": "생산중 가동내역을 삭제했습니다. 배정된 LOT 예약도 해제되었습니다.",
    }
