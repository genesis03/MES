from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import get_current_user_optional
from models.models import ProcessModel

router = APIRouter(tags=["Admin Process Code"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _is_super_admin(user) -> bool:
    if not user:
        return False
    username = str(getattr(user, "username", "")).strip().lower()
    role = str(getattr(user, "role", "")).strip().upper()
    return username == "admin" or role == "SUPERADMIN"


def _is_admin(user) -> bool:
    if not user:
        return False
    role = str(getattr(user, "role", "")).strip().upper()
    return role in {"ADMIN", "SUPERADMIN"} or str(getattr(user, "username", "")).strip().lower() == "admin"


@router.get("/admin/processes-locations", response_class=HTMLResponse)
async def admin_processes_locations_override(request: Request, db: Session = Depends(get_db)):
    """기존 관리자 공정 화면을 유지하되 최고관리자에게만 코드 변경 UI를 추가합니다."""
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not _is_admin(user):
        return RedirectResponse(url="/shipping", status_code=303)

    template_name = "admin_process_location_super.html" if _is_super_admin(user) else "admin_process_location.html"
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"request": request, "user": user, "is_super_admin": _is_super_admin(user)},
    )


@router.post("/api/admin/processes/change-code")
async def change_process_code(request: Request, db: Session = Depends(get_db)):
    """최고관리자 전용 공정코드 일괄 변경.

    새 공정코드의 부모 레코드를 먼저 만든 뒤 관련 테이블의 process_code 및
    item_master.production_loc 참조값을 한 트랜잭션에서 변경하고 기존 공정을 삭제합니다.
    """
    user = get_current_user_optional(request, db)
    if not _is_super_admin(user):
        raise HTTPException(status_code=403, detail="최고관리자만 공정 코드를 변경할 수 있습니다.")

    body = await request.json()
    process_id = body.get("id")
    new_code = str(body.get("process_code", "")).strip().upper()
    new_name = str(body.get("process_name", "")).strip()
    sort_order = int(body.get("sort_order") or 1)
    is_active = str(body.get("is_active", "Y")).strip().upper()
    note = str(body.get("note", "")).strip() or None

    if not process_id or not new_code or not new_name:
        raise HTTPException(status_code=400, detail="공정 ID, 코드, 명칭은 필수입니다.")
    if is_active not in {"Y", "N"}:
        raise HTTPException(status_code=400, detail="사용 여부 값이 올바르지 않습니다.")

    target = db.query(ProcessModel).filter(ProcessModel.id == process_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="공정 정보를 찾을 수 없습니다.")

    old_code = str(target.process_code).strip()
    old_name = str(target.process_name).strip()

    if new_code == old_code:
        target.process_name = new_name
        target.sort_order = sort_order
        target.is_active = is_active
        target.note = note
        db.commit()
        return {"status": "success", "id": target.id, "message": "공정 정보가 수정되었습니다."}

    duplicate = db.query(ProcessModel).filter(ProcessModel.process_code == new_code).first()
    if duplicate:
        raise HTTPException(status_code=400, detail=f"이미 등록된 공정 코드입니다. ({new_code})")

    try:
        replacement = ProcessModel(
            process_code=new_code,
            process_name=new_name,
            sort_order=sort_order,
            is_active=is_active,
            note=note,
            created_at=target.created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        db.add(replacement)
        db.flush()

        inspector = inspect(db.get_bind())
        for table_name in inspector.get_table_names():
            if table_name == "processes":
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            target_columns = []
            if "process_code" in columns:
                target_columns.append("process_code")
            if table_name == "item_master" and "production_loc" in columns:
                target_columns.append("production_loc")

            for column_name in target_columns:
                db.execute(
                    text(
                        f'UPDATE "{table_name}" '
                        f'SET "{column_name}" = :new_code '
                        f'WHERE "{column_name}" = :old_code OR "{column_name}" = :old_name'
                    ),
                    {"new_code": new_code, "old_code": old_code, "old_name": old_name},
                )

        db.delete(target)
        db.commit()
        return {
            "status": "success",
            "id": replacement.id,
            "old_code": old_code,
            "new_code": new_code,
            "message": f"공정 코드가 {old_code} → {new_code}(으)로 일괄 변경되었습니다.",
        }
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="공정 코드 변경 중 참조 무결성 충돌이 발생했습니다. 연결 데이터를 확인해 주세요.",
        ) from exc
