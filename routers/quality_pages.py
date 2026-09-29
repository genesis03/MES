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


@router.get("/quality/production-defects", response_class=HTMLResponse)
def production_defects_page(request: Request, current_user=Depends(get_current_user)):
    username = str(getattr(current_user, "username", "") or "").strip().lower()
    role = str(getattr(current_user, "role", "") or "").strip().upper()
    return templates.TemplateResponse(
        request=request,
        name="quality_production_defects.html",
        context={
            "request": request,
            "user": current_user,
            "is_admin": username == "admin" or role in {"ADMIN", "SUPERADMIN"},
        },
    )
