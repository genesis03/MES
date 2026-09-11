from typing import Optional
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import get_current_user_optional, check_permission
from models.models import ShippingMasterModel, UserModel

router = APIRouter(tags=["Pages"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

def get_first_accessible_url(user: UserModel) -> Optional[str]:
    """사용자가 접근 가능한 최초의 메뉴 URL을 탐색하여 반환"""
    if str(user.role).strip().upper() == "ADMIN":
        return "/shipping/history"

    menu_checks = [
        ("history", "/shipping/history"),
        ("print", "/shipping/print"),
        ("manual", "/shipping/manual"),
        ("verify", "/shipping/verify")
    ]

    for child_menu, url in menu_checks:
        if check_permission(user, "shipping", child_menu, "READ"):
            return url

    return None

def handle_unauthorized_access(user: UserModel):
    """권한 부족 시 사용 가능한 메뉴로 복귀시키거나, 권한이 전무할 경우 로그아웃 처리"""
    fallback_url = get_first_accessible_url(user)
    if fallback_url:
        return HTMLResponse(
            f"<script>alert('해당 메뉴에 대한 접근 권한이 없습니다.'); location.href='{fallback_url}';</script>",
            status_code=403
        )
    return HTMLResponse(
        "<script>alert('부여된 시스템 메뉴 권한이 없습니다. 관리자에게 문의하십시오.'); location.href='/logout';</script>",
        status_code=403
    )

# ==============================================================================
# 1. 디스패처 (초기 진입점: 권한에 따른 자동 분기 처리)
# ==============================================================================
@router.get("/", response_class=HTMLResponse)
@router.get("/shipping", response_class=HTMLResponse)
async def dispatch_entry_point(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    target_url = get_first_accessible_url(user)
    if target_url:
        return RedirectResponse(url=target_url, status_code=303)

    return handle_unauthorized_access(user)

# ==============================================================================
# 2. 개별 업무 화면 라우터
# ==============================================================================
@router.get("/shipping/history", response_class=HTMLResponse)
async def history_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_permission(user, "shipping", "history", "READ"):
        return handle_unauthorized_access(user)

    data_count = db.query(ShippingMasterModel).count()
    return templates.TemplateResponse(
        request=request, 
        name="history.html", 
        context={"data_count": data_count, "user": user}
    )

@router.get("/shipping/manual", response_class=HTMLResponse)
@router.get("/manual", response_class=HTMLResponse)
async def manual_print_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_permission(user, "shipping", "manual", "READ"):
        return handle_unauthorized_access(user)

    return templates.TemplateResponse(
        request=request, 
        name="label_manual.html", 
        context={"user": user}
    )

@router.get("/shipping/print", response_class=HTMLResponse)
@router.get("/print", response_class=HTMLResponse)
async def print_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_permission(user, "shipping", "print", "READ"):
        return handle_unauthorized_access(user)

    return templates.TemplateResponse(
        request=request, 
        name="label_print.html", 
        context={"user": user}
    )

@router.get("/shipping/verify", response_class=HTMLResponse)
@router.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_permission(user, "shipping", "verify", "READ"):
        return handle_unauthorized_access(user)

    data_count = db.query(ShippingMasterModel).count()
    return templates.TemplateResponse(
        request=request, 
        name="verify.html", 
        context={"data_count": data_count, "user": user}
    )