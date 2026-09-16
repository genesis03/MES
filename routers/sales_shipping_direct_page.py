from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.security import get_current_user

router = APIRouter(tags=["Sales Shipping Direct Page"])
templates = Jinja2Templates(directory="templates")


@router.get("/sales/shipping", response_class=HTMLResponse)
def shipping_entry_direct_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="sales_shipping_direct.html",
        context={"request": request, "user": current_user},
    )
