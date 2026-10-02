from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from core.security import check_admin_permission, get_current_user, parse_user_permissions

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


@router.get("/quality/defect-status", response_class=HTMLResponse)
def defect_status_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="quality_defect_status.html",
        context={"request": request, "user": current_user},
    )


# 화면 안내만 정의합니다. 검사기준서/성적서 데이터 및 작성 API는 아직 추가하지 않습니다.
PLANNED_INSPECTION_PAGES = {
    "/quality/patrol-inspections": {
        "title": "공정순회 검사",
        "section": "품질관리 · 검사 및 성적서",
        "description": "공정순회 검사와 성적서를 작성하는 기능을 준비 중입니다.",
        "detail": "향후 해당 품목의 공정순회 검사기준서와 연결하여 검사항목·규격을 가져오고 측정값 외 추가 입력을 최소화합니다. 사용한 기준서 Revision과 당시 기준은 보존합니다.",
    },
    "/quality/inbound-reports": {
        "title": "입고 성적서",
        "section": "품질관리 · 검사 및 성적서",
        "description": "입고 성적서를 작성하는 기능을 준비 중입니다.",
        "detail": "향후 해당 품목의 입고검사 기준서와 연결하여 검사항목·규격을 가져오고 측정값 외 추가 입력을 최소화합니다. 사용한 기준서 Revision과 당시 기준은 보존합니다.",
    },
    "/quality/outbound-reports": {
        "title": "출하 성적서",
        "section": "품질관리 · 검사 및 성적서",
        "description": "출하 성적서를 작성하는 기능을 준비 중입니다.",
        "detail": "향후 해당 품목의 출하검사 기준서와 연결하여 검사항목·규격을 가져오고 측정값 외 추가 입력을 최소화합니다. 사용한 기준서 Revision과 당시 기준은 보존합니다.",
    },
}


@router.get("/quality/patrol-inspections", response_class=HTMLResponse)
@router.get("/quality/inbound-reports", response_class=HTMLResponse)
@router.get("/quality/outbound-reports", response_class=HTMLResponse)
def planned_inspection_page(request: Request, current_user=Depends(get_current_user)):
    if not check_admin_permission(current_user):
        access = parse_user_permissions(current_user).get("menu_access")
        value = access.get(request.url.path) if isinstance(access, dict) else None
        level = "READ" if value is True else str(value or "NONE").upper()
        if level not in {"READ", "WRITE"}:
            raise HTTPException(403, "해당 검사/성적서 메뉴에 대한 접근 권한이 없습니다.")
    return templates.TemplateResponse(
        request=request,
        name="standard_documents/planned.html",
        context={"request": request, "user": current_user,
                 "page": PLANNED_INSPECTION_PAGES[request.url.path]},
    )
