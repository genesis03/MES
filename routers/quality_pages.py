from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.security import get_current_user

router = APIRouter(tags=["Quality Pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/quality/inbound-defects", response_class=HTMLResponse)
def inbound_defects_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="quality_inbound_defects.html",
        context={
            "request": request,
            "user": current_user,
        },
    )
