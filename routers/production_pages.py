from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.security import get_current_user

router = APIRouter(tags=["Production Pages"])
templates = Jinja2Templates(directory="templates")


PRODUCTION_PAGES = {
    "/production/rework": (
        "수정품 가공 처리 (리워크)",
        "부적합품 재가공과 -R LOT 이력을 관리하는 화면입니다.",
    ),
    "/production/packing": (
        "포장(출고LOT) 처리",
        "조립 완료품을 박스 단위로 포장하고 출고 LOT를 생성하는 화면입니다.",
    ),
    "/production/equipment/status": (
        "설비 가동 현황 (OEE)",
        "호기별 가동·비가동과 설비효율을 조회하는 화면입니다.",
    ),
}


def _render_production_ready(request: Request, current_user, route_path: str):
    page_title, page_description = PRODUCTION_PAGES[route_path]
    return templates.TemplateResponse(
        request=request,
        name="production_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": page_title,
            "page_description": page_description,
            "route_path": route_path,
        },
    )


def _production_run_page(request: Request, current_user, performance_type: str):
    is_assembly = performance_type == "ASSEMBLY"
    return templates.TemplateResponse(
        request=request,
        name="production_machining.html",
        context={
            "request": request,
            "user": current_user,
            "performance_type": performance_type,
            "page_title": "조립 실적 등록" if is_assembly else "가공 실적 등록",
            "action_label": "조립내역" if is_assembly else "가공내역",
        },
    )


@router.get("/production/plans", response_class=HTMLResponse)
def production_plans_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="production_plans.html",
        context={"request": request, "user": current_user},
    )


@router.get("/production/orders", response_class=HTMLResponse)
def production_orders_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="production_orders.html",
        context={"request": request, "user": current_user},
    )


@router.get("/production/orders/inquiry", response_class=HTMLResponse)
def production_orders_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    username = str(getattr(current_user, "username", "")).strip().lower()
    role = str(getattr(current_user, "role", "")).strip().upper()
    return templates.TemplateResponse(
        request=request,
        name="production_orders_inquiry.html",
        context={
            "request": request,
            "user": current_user,
            "is_super_admin": username == "admin" or role == "SUPERADMIN",
        },
    )


@router.get("/production/performance/machining", response_class=HTMLResponse)
def production_machining_page(request: Request, current_user=Depends(get_current_user)):
    return _production_run_page(request, current_user, "MACHINING")


@router.get("/production/performance/assembly", response_class=HTMLResponse)
def production_assembly_page(request: Request, current_user=Depends(get_current_user)):
    return _production_run_page(request, current_user, "ASSEMBLY")


@router.get("/production/rework", response_class=HTMLResponse)
def production_rework_page(request: Request, current_user=Depends(get_current_user)):
    return _render_production_ready(request, current_user, "/production/rework")


@router.get("/production/packing", response_class=HTMLResponse)
def production_packing_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="production_packing.html",
        context={"request": request, "user": current_user},
    )


@router.get("/production/performance/status", response_class=HTMLResponse)
def production_status_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="production_performance_status.html",
        context={"request": request, "user": current_user},
    )


@router.get("/production/equipment/status", response_class=HTMLResponse)
def production_equipment_status_page(request: Request, current_user=Depends(get_current_user)):
    return _render_production_ready(request, current_user, "/production/equipment/status")
