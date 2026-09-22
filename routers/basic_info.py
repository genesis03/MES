# routers/basic_info.py 상단부
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, Request, status, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import (
    get_current_user_optional,
    get_current_user,
    require_admin_user,
    check_permission,
    parse_user_permissions,
)
# 실제 존재하는 품목/공정 마스터 모델만 import
from models.models import ItemMasterModel, ProcessModel
from services.item_identity_service import rename_item_part_no

# 절대 경로 기준 templates 디렉터리 설정
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

# --------------------------------------------------------------------------
# 이 아래부터 기존 @router.get("/basic-info/items", ...) 코드가 이어집니다.
# --------------------------------------------------------------------------

def check_basic_info_permission(user: Any) -> bool:
    """기초 정보 메뉴 접근 권한 확인"""
    if not user:
        return False
    if str(getattr(user, "role", "")).strip().upper() == "ADMIN":
        return True
    return check_permission(user, "basic_info", "READ")


def _normalize_process_code(db: Session, raw_value: Any) -> Optional[str]:
    """품목 공정 입력값(코드 또는 명칭)을 process_code로 정규화합니다."""
    value = str(raw_value or "").strip()
    if not value:
        return None

    process = (
        db.query(ProcessModel)
        .filter(
            (ProcessModel.process_code == value)
            | (ProcessModel.process_name == value)
        )
        .first()
    )
    if not process:
        raise HTTPException(status_code=400, detail=f"등록되지 않은 공정입니다. ({value})")
    return process.process_code


# 1. 화면 렌더링 라우터
@router.get("/basic-info/items", response_class=HTMLResponse)
async def item_master_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_basic_info_permission(user):
        return RedirectResponse(url="/shipping", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="item_master.html",
        context={"user": user}
    )


# 2. 품목 목록 조회 API (계정유형, 자재유형 필터 포함)
@router.get("/api/basic-info/items")
async def get_items(
    request: Request,
    part_no: Optional[str] = None,
    part_name: Optional[str] = None,
    account_type: Optional[str] = None,
    material_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not check_basic_info_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    query = db.query(ItemMasterModel)

    if part_no:
        query = query.filter(ItemMasterModel.part_no.ilike(f"%{part_no.strip()}%"))
    if part_name:
        query = query.filter(ItemMasterModel.part_name.ilike(f"%{part_name.strip()}%"))
    if account_type:
        query = query.filter(ItemMasterModel.account_type == account_type.strip())
    if material_type:
        query = query.filter(ItemMasterModel.material_type == material_type.strip())

    items = query.order_by(ItemMasterModel.part_no.asc()).all()
    process_rows = db.query(ProcessModel).all()
    process_name_by_code = {p.process_code: p.process_name for p in process_rows}

    data = [
        {
            "id": it.id,
            "part_no": it.part_no,
            "vehicle_model": it.vehicle_model or "",
            "part_name": it.part_name,
            "revision": it.revision,
            "spec": it.spec or "",
            "account_type": it.account_type,
            "material_type": it.material_type,
            "item_group": it.item_group or "",
            "unit": it.unit,
            "snp": it.snp or 0,
            "moq": it.moq or 0,
            "safety_stock": it.safety_stock or 0,
            "weight": it.weight or 0.0,
            "inbound_loc": it.inbound_loc or "",
            # 기존 화면은 공정명을 표시/선택하므로 표시값은 명칭으로 유지합니다.
            # DB에는 production_loc을 process_code로 저장합니다.
            "production_loc": process_name_by_code.get(it.production_loc, it.production_loc or ""),
            "production_loc_code": it.production_loc or "",
            "is_active": it.is_active,
            "note": it.note or "",
            "created_at": it.created_at,
            "updated_at": it.updated_at or ""
        }
        for it in items
    ]

    return {"status": "success", "data": data}


# 3. 품목 등록 API
@router.post("/api/basic-info/items/create")
async def create_item(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_basic_info_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    body = await request.json()
    part_no = str(body.get("part_no", "")).strip()
    part_name = str(body.get("part_name", "")).strip()
    account_type = str(body.get("account_type", "")).strip()
    material_type = str(body.get("material_type", "")).strip()

    if not part_no or not part_name or not account_type or not material_type:
        raise HTTPException(status_code=400, detail="품번, 품명, 계정유형, 자재유형은 필수 입력 항목입니다.")

    exists = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"이미 등록된 품번입니다. ({part_no})")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    new_item = ItemMasterModel(
        part_no=part_no,
        vehicle_model=str(body.get("vehicle_model", "")).strip() or None,
        part_name=part_name,
        revision=str(body.get("revision", "Rev.00")).strip(),
        spec=str(body.get("spec", "")).strip() or None,
        account_type=account_type,
        material_type=material_type,
        item_group=str(body.get("item_group", "")).strip() or None,
        unit=str(body.get("unit", "EA")).strip(),
        snp=int(body.get("snp") or 0),
        moq=int(body.get("moq") or 0),
        safety_stock=int(body.get("safety_stock") or 0),
        weight=float(body.get("weight") or 0.0),
        inbound_loc=str(body.get("inbound_loc", "")).strip() or None,
        production_loc=_normalize_process_code(db, body.get("production_loc")),
        is_active=str(body.get("is_active", "Y")).upper(),
        note=str(body.get("note", "")).strip() or None,
        created_at=now_str,
        updated_at=now_str
    )

    db.add(new_item)
    db.commit()
    db.refresh(new_item)

    return {"status": "success", "message": "품목이 성공적으로 등록되었습니다.", "id": new_item.id}


# 4. 품목 수정 API
@router.post("/api/basic-info/items/update")
async def update_item(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_basic_info_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    body = await request.json()
    item_id = body.get("id")
    if not item_id:
        raise HTTPException(status_code=400, detail="품목 식별자(ID)가 누락되었습니다.")

    target = db.query(ItemMasterModel).filter(ItemMasterModel.id == item_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="해당 품목을 찾을 수 없습니다.")

    new_part_no = str(body.get("part_no", target.part_no)).strip()
    if not new_part_no:
        raise HTTPException(status_code=400, detail="품번은 필수 입력 항목입니다.")

    if new_part_no != target.part_no:
        changed_by = (
            str(getattr(user, "name", "") or getattr(user, "username", "") or "").strip()
            or None
        )
        try:
            rename_item_part_no(
                db,
                target,
                new_part_no,
                changed_by=changed_by,
                reason=str(body.get("part_no_change_reason", "")).strip() or "품목마스터 수정",
            )
        except ValueError as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    target.vehicle_model = str(body.get("vehicle_model", "")).strip() or None
    target.part_name = str(body.get("part_name", target.part_name)).strip()
    target.revision = str(body.get("revision", target.revision)).strip()
    target.spec = str(body.get("spec", "")).strip() or None
    target.account_type = str(body.get("account_type", target.account_type)).strip()
    target.material_type = str(body.get("material_type", target.material_type)).strip()
    target.item_group = str(body.get("item_group", "")).strip() or None
    target.unit = str(body.get("unit", target.unit)).strip()
    target.snp = int(body.get("snp") or 0)
    target.moq = int(body.get("moq") or 0)
    target.safety_stock = int(body.get("safety_stock") or 0)
    target.weight = float(body.get("weight") or 0.0)
    target.inbound_loc = str(body.get("inbound_loc", "")).strip() or None
    target.production_loc = _normalize_process_code(db, body.get("production_loc"))
    target.is_active = str(body.get("is_active", target.is_active)).upper()
    target.note = str(body.get("note", "")).strip() or None
    target.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.commit()

    return {"status": "success", "message": "품목 정보가 성공적으로 수정되었습니다."}


# 5. 품목 삭제 API
@router.post("/api/basic-info/items/delete")
async def delete_item(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_basic_info_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    body = await request.json()
    item_id = body.get("id")
    if not item_id:
        raise HTTPException(status_code=400, detail="품목 식별자(ID)가 누락되었습니다.")

    target = db.query(ItemMasterModel).filter(ItemMasterModel.id == item_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="해당 품목을 찾을 수 없습니다.")

    db.delete(target)
    db.commit()

    return {"status": "success", "message": "품목이 삭제되었습니다."}
