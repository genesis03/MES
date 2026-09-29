from sqlalchemy import func
from sqlalchemy.orm import Session

from models.production_defect import QualityProductionDefect


def active_production_defect_qty(
    db: Session,
    lot_no: str,
    item_id: int | None = None,
) -> float:
    query = db.query(
        func.coalesce(func.sum(QualityProductionDefect.defect_qty), 0.0)
    ).filter(
        QualityProductionDefect.lot_no == lot_no,
        QualityProductionDefect.status == "ACTIVE",
    )
    if item_id:
        query = query.filter(QualityProductionDefect.item_id == item_id)
    return float(query.scalar() or 0.0)
