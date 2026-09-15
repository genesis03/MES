from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.production import ProductionPerformance
from routers import production_run
from services.production_lot_service import ensure_output_lot_for_performance

router = APIRouter(prefix="/api/production-run", tags=["Production Run LOT Finalize"])


@router.post("/{run_id}/complete")
def complete_run_with_output_lot(
    run_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = production_run.complete_run(run_id=run_id, db=db, current_user=current_user)
    performance_id = result.get("performance_id")
    performance = db.get(ProductionPerformance, performance_id) if performance_id else None
    if not performance:
        raise HTTPException(500, "생산실적은 저장되었지만 생성 LOT 정보를 찾을 수 없습니다.")

    output_lot = ensure_output_lot_for_performance(db, performance)
    db.commit()
    result["output_lot_no"] = output_lot.lot_no
    result["message"] = f"생산실적이 등록되고 생산 LOT {output_lot.lot_no}가 생성되었습니다."
    return result
