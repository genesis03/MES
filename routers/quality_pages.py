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
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "입고 품목 불량 처리",
            "page_description": "입고 LOT별 불량 수량과 품질 처리 이력을 관리하는 화면입니다. 기능은 품질관리 단계에서 구현합니다.",
            "route_path": "/quality/inbound-defects",
        },
    )
