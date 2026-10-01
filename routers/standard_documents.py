"""표준문서 메뉴의 개발 예정 화면. 문서 작성/저장 API는 아직 제공하지 않습니다."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import check_admin_permission, get_current_user_optional, parse_user_permissions
from services.inspection_standard_access import (
    INSPECTION_MENU_PATH, STANDARD_PATH, MASTER_PATH, inspection_level,
)

router = APIRouter(tags=["Standard Documents"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 화면 제목/안내만 정의합니다. 업무 마스터나 문서 유형 공통코드를 대신하지 않습니다.
PLANNED_PAGES = {
    "/standard-documents/control-plans": {
        "title": "CP 관리",
        "description": "품목·공정별 관리계획서를 관리하는 기능을 준비 중입니다.",
    },
    "/standard-documents/work-standards": {
        "title": "작업표준서 관리",
        "description": "품목·공정별 작업표준서와 개정 이력을 관리하는 기능을 준비 중입니다.",
    },
    "/standard-documents/packaging-specifications": {
        "title": "포장사양서 관리",
        "description": "품목별 포장사양서와 개정 이력을 관리하는 기능을 준비 중입니다.",
    },
}


@router.get("/standard-documents/control-plans", response_class=HTMLResponse)
@router.get("/standard-documents/work-standards", response_class=HTMLResponse)
@router.get("/standard-documents/packaging-specifications", response_class=HTMLResponse)
def planned_document_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not check_admin_permission(user):
        access = parse_user_permissions(user).get("menu_access")
        value = access.get(request.url.path) if isinstance(access, dict) else None
        level = "READ" if value is True else str(value or "NONE").upper()
        if level not in {"READ", "WRITE"}:
            raise HTTPException(403, "해당 표준문서 메뉴에 대한 접근 권한이 없습니다.")
    page = PLANNED_PAGES[request.url.path]
    return templates.TemplateResponse(
        request=request, name="standard_documents/planned.html",
        context={"user": user, "page": page},
    )


@router.get(INSPECTION_MENU_PATH, response_class=HTMLResponse)
def inspection_standards_entry(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    for path in (STANDARD_PATH, MASTER_PATH):
        if inspection_level(user, path) in {"READ", "WRITE"}:
            return RedirectResponse(path, status_code=303)
    raise HTTPException(403, "검사기준서 관리 조회 권한이 없습니다.")
