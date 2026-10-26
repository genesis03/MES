"""Preserve every saved legacy plan month as Rev.00 before serving requests."""
from sqlalchemy.orm import Session

from models.production_month_plan import ProductionMonthPlanLine
from models.production_plan_revision import ProductionPlanRevisionState
from services.oee_schedule import month_days
from services.production_plan_revisions import ensure


def ensure_existing_plan_revisions(engine):
    with Session(engine) as db:
        months = [month for month, in db.query(ProductionMonthPlanLine.month)
                  .outerjoin(ProductionPlanRevisionState,
                             ProductionPlanRevisionState.month == ProductionMonthPlanLine.month)
                  .filter(ProductionPlanRevisionState.month.is_(None))
                  .distinct().order_by(ProductionMonthPlanLine.month)]
        for month in months:
            month_days(month)
            ensure(db, month)
    return len(months)
