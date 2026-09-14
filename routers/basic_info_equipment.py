from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import check_permission, get_current_user_optional
from models.equipment import EquipmentMaster
from models.models import ProcessModel

router = APIRouter(tags=["Equipment Master"])
templates = Jinja2Templates(directory="templates")


class EquipmentPayload(BaseModel):
    equipment_code: str
    equipment_name: str
    process_code: str
    machine_no: str
    is_active: str = "Y"
    sort_order: int = 1
    note: Optional[str] = None


def _can_read(user: Any) -> bool:
    if not user:
        return False
    if str(getattr(user, "role", "")).strip().upper() == "ADMIN":
        return True
    return check_permission(user, "basic_info", "READ")


def _validate_process(db: Session, process_code: str) -> ProcessModel:
    code = process_code.strip()
    process = (
        db.query(ProcessModel)
        .filter(ProcessModel.process_code == code, ProcessModel.is_active == "Y")
        .first()
    )
    if not process:
        raise HTTPException(status_code=400, detail="사용 가능한 제조 공정을 선택해 주세요.")
    return process


def _serialize(row: EquipmentMaster, process_name: str = ""):
    return {
        "id": row.id,
        "equipment_code": row.equipment_code,
        "equipment_name": row.equipment_name,
        "process_code": row.process_code,
        "process_name": process_name,
        "machine_no": row.machine_no,
        "is_active": row.is_active,
        "sort_order": row.sort_order,
        "note": row.note or "",
    }


@router.get("/basic-info/equipment", response_class=HTMLResponse)
def equipment_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not _can_read(user):
        return RedirectResponse(url="/shipping", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="equipment_master.html",
        context={"request": request, "user": user},
    )


@router.get("/api/basic-info/equipment/processes")
def equipment_process_options(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    rows = (
        db.query(ProcessModel)
        .filter(ProcessModel.is_active == "Y")
        .order_by(ProcessModel.sort_order.asc(), ProcessModel.process_code.asc())
        .all()
    )
    return [{"process_code": x.process_code, "process_name": x.process_name} for x in rows]


@router.get("/api/basic-info/equipment")
def list_equipment(
    request: Request,
    process_code: Optional[str] = None,
    keyword: Optional[str] = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    query = (
        db.query(EquipmentMaster, ProcessModel.process_name)
        .join(ProcessModel, ProcessModel.process_code == EquipmentMaster.process_code)
    )
    if process_code:
        query = query.filter(EquipmentMaster.process_code == process_code.strip())
    if active_only:
        query = query.filter(EquipmentMaster.is_active == "Y")
    if keyword:
        value = keyword.strip()
        query = query.filter(
            EquipmentMaster.equipment_code.contains(value, autoescape=True)
            | EquipmentMaster.equipment_name.contains(value, autoescape=True)
            | EquipmentMaster.machine_no.contains(value, autoescape=True)
        )

    rows = query.order_by(
        ProcessModel.sort_order.asc(),
        EquipmentMaster.sort_order.asc(),
        EquipmentMaster.equipment_code.asc(),
    ).all()
    return [_serialize(equipment, process_name) for equipment, process_name in rows]


@router.post("/api/basic-info/equipment")
def create_equipment(
    payload: EquipmentPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    code = payload.equipment_code.strip()
    name = payload.equipment_name.strip()
    machine_no = payload.machine_no.strip()
    if not code or not name or not machine_no:
        raise HTTPException(status_code=400, detail="설비코드, 설비명, 호기는 필수입니다.")
    process = _validate_process(db, payload.process_code)
    if db.query(EquipmentMaster.id).filter(EquipmentMaster.equipment_code == code).first():
        raise HTTPException(status_code=400, detail=f"이미 등록된 설비코드입니다. ({code})")
    if db.query(EquipmentMaster.id).filter(
        EquipmentMaster.process_code == process.process_code,
        EquipmentMaster.machine_no == machine_no,
    ).first():
        raise HTTPException(status_code=400, detail=f"{process.process_name} 공정의 {machine_no}호기는 이미 등록되어 있습니다.")

    active = payload.is_active.strip().upper()
    if active not in {"Y", "N"}:
        raise HTTPException(status_code=400, detail="사용여부 값이 올바르지 않습니다.")

    row = EquipmentMaster(
        equipment_code=code,
        equipment_name=name,
        process_code=process.process_code,
        machine_no=machine_no,
        is_active=active,
        sort_order=max(int(payload.sort_order or 1), 1),
        note=(payload.note or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"status": "success", "id": row.id, "message": "설비가 등록되었습니다."}


@router.put("/api/basic-info/equipment/{equipment_id}")
def update_equipment(
    equipment_id: int,
    payload: EquipmentPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    row = db.get(EquipmentMaster, equipment_id)
    if not row:
        raise HTTPException(status_code=404, detail="설비를 찾을 수 없습니다.")

    code = payload.equipment_code.strip()
    name = payload.equipment_name.strip()
    machine_no = payload.machine_no.strip()
    if not code or not name or not machine_no:
        raise HTTPException(status_code=400, detail="설비코드, 설비명, 호기는 필수입니다.")
    process = _validate_process(db, payload.process_code)

    duplicate_code = db.query(EquipmentMaster.id).filter(
        EquipmentMaster.equipment_code == code,
        EquipmentMaster.id != equipment_id,
    ).first()
    if duplicate_code:
        raise HTTPException(status_code=400, detail=f"이미 등록된 설비코드입니다. ({code})")
    duplicate_machine = db.query(EquipmentMaster.id).filter(
        EquipmentMaster.process_code == process.process_code,
        EquipmentMaster.machine_no == machine_no,
        EquipmentMaster.id != equipment_id,
    ).first()
    if duplicate_machine:
        raise HTTPException(status_code=400, detail=f"{process.process_name} 공정의 {machine_no}호기는 이미 등록되어 있습니다.")

    active = payload.is_active.strip().upper()
    if active not in {"Y", "N"}:
        raise HTTPException(status_code=400, detail="사용여부 값이 올바르지 않습니다.")

    row.equipment_code = code
    row.equipment_name = name
    row.process_code = process.process_code
    row.machine_no = machine_no
    row.is_active = active
    row.sort_order = max(int(payload.sort_order or 1), 1)
    row.note = (payload.note or "").strip() or None
    db.commit()
    return {"status": "success", "message": "설비 정보가 수정되었습니다."}


@router.delete("/api/basic-info/equipment/{equipment_id}")
def delete_equipment(
    equipment_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")
    row = db.get(EquipmentMaster, equipment_id)
    if not row:
        raise HTTPException(status_code=404, detail="설비를 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return {"status": "success", "message": "설비가 삭제되었습니다."}
