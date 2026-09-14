from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ProcessModel, StorageLocationModel
from models.partner import Partner

router = APIRouter(tags=["Subcontract Pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/subcontract/orders", response_class=HTMLResponse)
def subcontract_orders_page(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    vendors = (
        db.query(Partner)
        .filter(Partner.is_active == "Y", Partner.partner_type.in_(["VENDOR", "BOTH"]))
        .order_by(Partner.partner_name.asc())
        .all()
    )
    processes = (
        db.query(ProcessModel)
        .filter(ProcessModel.is_active == "Y")
        .order_by(ProcessModel.sort_order, ProcessModel.process_code)
        .all()
    )
    storage_locations = (
        db.query(StorageLocationModel)
        .filter(StorageLocationModel.is_active == "Y")
        .order_by(StorageLocationModel.sort_order, StorageLocationModel.location_code)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="subcontract_orders.html",
        context={
            "request": request,
            "user": current_user,
            "vendors": vendors,
            "processes": processes,
            "storage_locations": storage_locations,
        },
    )
