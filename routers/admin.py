# routers/admin.py
import json
from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from models import UserModel, CommonCodeModel, ProcessModel, StorageLocationModel, WarehouseMasterModel
from core.config import BASE_DIR
from core.database import get_db
from core.security import (
    get_current_user_optional,
    require_admin_user,
    hash_password,
    parse_user_permissions
)

router = APIRouter(tags=["Admin"])
public_api_router = APIRouter(tags=["Public API"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ==============================================================================
# 관리자 권한 검증 헬퍼
# ==============================================================================

def check_admin_permission(user: UserModel) -> bool:
    """관리자 권한 검증 함수"""
    return bool(user and user.role == "ADMIN")


# ==============================================================================
# 1. 화면 렌더링 라우터
# ==============================================================================

@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if user.role != "ADMIN":
        return RedirectResponse(url="/shipping", status_code=303)
    return templates.TemplateResponse(request=request, name="admin_users.html", context={"user": user})


@router.get("/admin/common-codes", response_class=HTMLResponse)
async def admin_common_codes_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_admin_permission(user):
        return RedirectResponse(url="/shipping", status_code=303)
    return templates.TemplateResponse(request=request, name="admin_common_codes.html", context={"user": user})


@router.get("/admin/processes-locations", response_class=HTMLResponse)
@router.get("/admin/process-locations", response_class=HTMLResponse)
@router.get("/admin/process-location", response_class=HTMLResponse)
async def admin_processes_locations_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_admin_permission(user):
        return RedirectResponse(url="/shipping", status_code=303)
    return templates.TemplateResponse(request=request, name="admin_process_location.html", context={"user": user})


# ==============================================================================
# 2. 사용자 관리 CRUD API (HTML 스크립트와 1:1 매핑)
# ==============================================================================

@router.get("/api/admin/users")
async def get_user_list(db: Session = Depends(get_db), current_admin: UserModel = Depends(require_admin_user)):
    users = db.query(UserModel).order_by(UserModel.id.asc()).all()
    rows = [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "permissions": parse_user_permissions(u),
            "department": getattr(u, "department", "") or "",
            "name": getattr(u, "name", "") or "",
            "note": getattr(u, "note", "") or "",
            "created_at": getattr(u, "created_at", "") or "",
            "is_self": (u.id == current_admin.id)
        }
        for u in users
    ]
    return {"data": rows}


@router.post("/api/admin/users/create")
async def create_user(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: UserModel = Depends(require_admin_user)
):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()
    name = str(body.get("name", "")).strip()
    department = str(body.get("department", "")).strip()
    note = str(body.get("note", "")).strip()
    role = str(body.get("role", "USER")).strip().upper()
    perms = body.get("permissions", {})

    if not username or not password:
        raise HTTPException(status_code=400, detail="아이디와 비밀번호를 모두 입력해 주십시오.")
    if not name:
        raise HTTPException(status_code=400, detail="사용자 성명을 입력해 주십시오.")

    if db.query(UserModel).filter(UserModel.username == username).first():
        raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_user = UserModel(
        username=username,
        password_hash=hash_password(password),
        role=role,
        permissions=json.dumps(perms, ensure_ascii=False) if isinstance(perms, (dict, list)) else str(perms),
        department=department if department else None,
        name=name,
        note=note if note else None,
        created_at=now_str
    )
    db.add(new_user)
    db.commit()
    return {"status": "success", "message": "계정이 등록되었습니다."}


@router.post("/api/admin/users/update")
async def update_user(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: UserModel = Depends(require_admin_user)
):
    body = await request.json()
    user_id = body.get("id")
    name = str(body.get("name", "")).strip()
    department = str(body.get("department", "")).strip()
    note = str(body.get("note", "")).strip()
    role = str(body.get("role", "USER")).strip().upper()
    perms = body.get("permissions", {})
    new_password = str(body.get("password", "")).strip()

    target_user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="해당 사용자를 찾을 수 없습니다.")

    if not name:
        raise HTTPException(status_code=400, detail="사용자 성명을 입력해 주십시오.")

    if target_user.id == current_admin.id and role != "ADMIN":
        raise HTTPException(status_code=400, detail="본인 관리자 계정의 역할은 변경할 수 없습니다.")

    target_user.role = role
    target_user.permissions = json.dumps(perms, ensure_ascii=False) if isinstance(perms, (dict, list)) else str(perms)
    target_user.name = name
    target_user.department = department if department else None
    target_user.note = note if note else None

    # 비밀번호 입력란에 값이 채워져 있을 때만 해시 갱신
    if new_password:
        target_user.password_hash = hash_password(new_password)

    db.commit()
    return {"status": "success", "message": "계정 정보 및 권한이 저장되었습니다."}


@router.post("/api/admin/users/delete")
async def delete_user(
    request: Request,
    db: Session = Depends(get_db),
    current_admin: UserModel = Depends(require_admin_user)
):
    body = await request.json()
    user_id = body.get("id")

    target_user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="해당 사용자를 찾을 수 없습니다.")

    if target_user.id == current_admin.id:
        raise HTTPException(status_code=400, detail="현재 로그인 중인 본인 관리자 계정은 삭제할 수 없습니다.")

    db.delete(target_user)
    db.commit()
    return {"status": "success", "message": "계정이 삭제되었습니다."}


# ==============================================================================
# 3. 콤보상자 공용 Lookup API (품목 마스터/BOM 연동)
# ==============================================================================

@public_api_router.get("/api/common-codes/lookup")
@router.get("/api/common-codes/lookup")
async def get_common_codes_lookup(db: Session = Depends(get_db)):
    codes = db.query(CommonCodeModel).filter(CommonCodeModel.is_active == "Y").order_by(CommonCodeModel.sort_order.asc(), CommonCodeModel.id.asc()).all()
    grouped = {}
    for c in codes:
        if c.group_code not in grouped:
            grouped[c.group_code] = []
        grouped[c.group_code].append({"code": c.code, "name": c.code_name})
    return {"status": "success", "data": grouped}


@public_api_router.get("/api/locations/lookup")
@router.get("/api/locations/lookup")
async def get_locations_lookup(db: Session = Depends(get_db)):
    locations = db.query(StorageLocationModel).filter(StorageLocationModel.is_active == "Y").order_by(StorageLocationModel.sort_order.asc(), StorageLocationModel.id.asc()).all()
    location_list = [loc.location_name for loc in locations]

    processes = db.query(ProcessModel).filter(ProcessModel.is_active == "Y").order_by(ProcessModel.sort_order.asc(), ProcessModel.id.asc()).all()
    process_list = [pr.process_name for pr in processes]

    warehouses = db.query(WarehouseMasterModel).filter(WarehouseMasterModel.is_active == "Y").order_by(WarehouseMasterModel.sort_order.asc(), WarehouseMasterModel.id.asc()).all()
    warehouse_list = [wh.warehouse_name for wh in warehouses]

    return {
        "status": "success",
        "data": {
            "inbound": location_list,
            "production": process_list,
            "warehouse": warehouse_list,
            "locations": location_list,
            "processes": process_list
        }
    }

# ==============================================================================
# 4. 공통 코드 CRUD API (관리자 전용)
# ==============================================================================

@router.get("/api/admin/common-codes")
async def get_all_common_codes(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    codes = db.query(CommonCodeModel).order_by(CommonCodeModel.group_code.asc(), CommonCodeModel.sort_order.asc()).all()
    data = [
        {
            "id": c.id,
            "group_code": c.group_code,
            "group_name": c.group_name,
            "code": c.code,
            "code_name": c.code_name,
            "sort_order": c.sort_order,
            "is_active": c.is_active,
            "note": c.note or "",
            "created_at": c.created_at
        }
        for c in codes
    ]
    return {"status": "success", "data": data}


@router.post("/api/admin/common-codes/create")
async def create_common_code(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    group_code = str(body.get("group_code", "")).strip().upper()
    group_name = str(body.get("group_name", "")).strip()
    code = str(body.get("code", "")).strip()
    code_name = str(body.get("code_name", "")).strip()

    if not group_code or not group_name or not code or not code_name:
        raise HTTPException(status_code=400, detail="그룹 코드, 그룹 명칭, 코드, 코드 명칭은 필수입니다.")

    exists = db.query(CommonCodeModel).filter(CommonCodeModel.group_code == group_code, CommonCodeModel.code == code).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"동일 그룹 내에 이미 등록된 코드입니다. ({code})")

    new_code = CommonCodeModel(
        group_code=group_code,
        group_name=group_name,
        code=code,
        code_name=code_name,
        sort_order=int(body.get("sort_order") or 1),
        is_active=str(body.get("is_active", "Y")).upper(),
        note=str(body.get("note", "")).strip() or None,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(new_code)
    db.commit()
    return {"status": "success", "message": "공통 코드가 등록되었습니다."}


@router.post("/api/admin/common-codes/update")
async def update_common_code(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(CommonCodeModel).filter(CommonCodeModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="해당 공통 코드를 찾을 수 없습니다.")

    target.group_name = str(body.get("group_name", target.group_name)).strip()
    target.code_name = str(body.get("code_name", target.code_name)).strip()
    target.sort_order = int(body.get("sort_order", target.sort_order))
    target.is_active = str(body.get("is_active", target.is_active)).upper()
    target.note = str(body.get("note", "")).strip() or None
    db.commit()
    return {"status": "success", "message": "공통 코드가 수정되었습니다."}


@router.post("/api/admin/common-codes/delete")
async def delete_common_code(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(CommonCodeModel).filter(CommonCodeModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="해당 공통 코드를 찾을 수 없습니다.")
    db.delete(target)
    db.commit()
    return {"status": "success", "message": "공통 코드가 삭제되었습니다."}


# ==============================================================================
# 5. 공정(Process) 마스터 CRUD API (관리자 전용)
# ==============================================================================

@router.get("/api/admin/processes")
async def get_processes(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    processes = db.query(ProcessModel).order_by(ProcessModel.sort_order.asc(), ProcessModel.id.asc()).all()
    data = [
        {
            "id": p.id,
            "process_code": p.process_code,
            "process_name": p.process_name,
            "sort_order": p.sort_order,
            "is_active": p.is_active,
            "note": p.note or "",
            "created_at": p.created_at
        }
        for p in processes
    ]
    return {"status": "success", "data": data}


@router.post("/api/admin/processes/create")
async def create_process(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    code = str(body.get("process_code", "")).strip().upper()
    name = str(body.get("process_name", "")).strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="공정 코드와 명칭은 필수입니다.")
    if db.query(ProcessModel).filter(ProcessModel.process_code == code).first():
        raise HTTPException(status_code=400, detail=f"이미 등록된 공정 코드입니다. ({code})")

    new_process = ProcessModel(
        process_code=code,
        process_name=name,
        sort_order=int(body.get("sort_order") or 1),
        is_active=str(body.get("is_active", "Y")).upper(),
        note=str(body.get("note", "")).strip() or None,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(new_process)
    db.commit()
    return {"status": "success", "message": "공정이 등록되었습니다."}


@router.post("/api/admin/processes/update")
async def update_process(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(ProcessModel).filter(ProcessModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="공정 정보를 찾을 수 없습니다.")

    target.process_name = str(body.get("process_name", target.process_name)).strip()
    target.sort_order = int(body.get("sort_order", target.sort_order))
    target.is_active = str(body.get("is_active", target.is_active)).upper()
    target.note = str(body.get("note", "")).strip() or None
    db.commit()
    return {"status": "success", "message": "공정 정보가 수정되었습니다."}


@router.post("/api/admin/processes/delete")
async def delete_process(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(ProcessModel).filter(ProcessModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="공정 정보를 찾을 수 없습니다.")
    db.delete(target)
    db.commit()
    return {"status": "success", "message": "공정이 삭제되었습니다."}


# ==============================================================================
# 6. 저장위치(Storage Location) 마스터 CRUD API (관리자 전용)
# ==============================================================================

@router.get("/api/admin/storage-locations")
async def get_storage_locations(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    locations = db.query(StorageLocationModel).order_by(StorageLocationModel.sort_order.asc(), StorageLocationModel.id.asc()).all()
    data = [
        {
            "id": loc.id,
            "location_code": loc.location_code,
            "location_name": loc.location_name,
            "sort_order": loc.sort_order,
            "is_active": loc.is_active,
            "note": loc.note or "",
            "created_at": loc.created_at
        }
        for loc in locations
    ]
    return {"status": "success", "data": data}


@router.post("/api/admin/storage-locations/create")
async def create_storage_location(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    code = str(body.get("location_code", "")).strip().upper()
    name = str(body.get("location_name", "")).strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="저장위치 코드와 명칭은 필수입니다.")
    if db.query(StorageLocationModel).filter(StorageLocationModel.location_code == code).first():
        raise HTTPException(status_code=400, detail=f"이미 등록된 저장위치 코드입니다. ({code})")

    new_loc = StorageLocationModel(
        location_code=code,
        location_name=name,
        sort_order=int(body.get("sort_order") or 1),
        is_active=str(body.get("is_active", "Y")).upper(),
        note=str(body.get("note", "")).strip() or None,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(new_loc)
    db.commit()
    return {"status": "success", "message": "저장위치가 등록되었습니다."}


@router.post("/api/admin/storage-locations/update")
async def update_storage_location(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(StorageLocationModel).filter(StorageLocationModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="저장위치 정보를 찾을 수 없습니다.")

    target.location_name = str(body.get("location_name", target.location_name)).strip()
    target.sort_order = int(body.get("sort_order", target.sort_order))
    target.is_active = str(body.get("is_active", target.is_active)).upper()
    target.note = str(body.get("note", "")).strip() or None
    db.commit()
    return {"status": "success", "message": "저장위치 정보가 수정되었습니다."}


@router.post("/api/admin/storage-locations/delete")
async def delete_storage_location(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(StorageLocationModel).filter(StorageLocationModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="저장위치 정보를 찾을 수 없습니다.")
    db.delete(target)
    db.commit()
    return {"status": "success", "message": "저장위치가 삭제되었습니다."}


# ==============================================================================
# 7. 창고(Warehouse) 마스터 CRUD API (관리자 전용)
# ==============================================================================

@router.get("/api/admin/warehouses-master")
@router.get("/api/admin/warehouses")
async def get_warehouses_master(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    warehouses = db.query(WarehouseMasterModel).order_by(WarehouseMasterModel.sort_order.asc(), WarehouseMasterModel.id.asc()).all()
    data = [
        {
            "id": w.id,
            "warehouse_code": w.warehouse_code,
            "warehouse_name": w.warehouse_name,
            "sort_order": w.sort_order,
            "is_active": w.is_active,
            "note": w.note or "",
            "created_at": w.created_at
        }
        for w in warehouses
    ]
    return {"status": "success", "data": data}


@router.post("/api/admin/warehouses-master/create")
async def create_warehouse_master(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    code = str(body.get("warehouse_code", "")).strip().upper()
    name = str(body.get("warehouse_name", "")).strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="창고 코드와 명칭은 필수입니다.")
    if db.query(WarehouseMasterModel).filter(WarehouseMasterModel.warehouse_code == code).first():
        raise HTTPException(status_code=400, detail=f"이미 등록된 창고 코드입니다. ({code})")

    new_wh = WarehouseMasterModel(
        warehouse_code=code,
        warehouse_name=name,
        sort_order=int(body.get("sort_order") or 1),
        is_active=str(body.get("is_active", "Y")).upper(),
        note=str(body.get("note", "")).strip() or None,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(new_wh)
    db.commit()
    return {"status": "success", "message": "창고가 등록되었습니다."}


@router.post("/api/admin/warehouses-master/update")
async def update_warehouse_master(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(WarehouseMasterModel).filter(WarehouseMasterModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="창고 정보를 찾을 수 없습니다.")

    target.warehouse_name = str(body.get("warehouse_name", target.warehouse_name)).strip()
    target.sort_order = int(body.get("sort_order", target.sort_order))
    target.is_active = str(body.get("is_active", target.is_active)).upper()
    target.note = str(body.get("note", "")).strip() or None
    db.commit()
    return {"status": "success", "message": "창고 정보가 수정되었습니다."}


@router.post("/api/admin/warehouses-master/delete")
async def delete_warehouse_master(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_admin_permission(user):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    body = await request.json()
    target = db.query(WarehouseMasterModel).filter(WarehouseMasterModel.id == body.get("id")).first()
    if not target:
        raise HTTPException(status_code=404, detail="창고 정보를 찾을 수 없습니다.")
    db.delete(target)
    db.commit()
    return {"status": "success", "message": "창고가 삭제되었습니다."}