from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import check_permission, get_current_user_optional
from models.models import ProcessModel
from models.worker import WorkerMaster, WorkerProcess

router = APIRouter(tags=["Worker Master"])
templates = Jinja2Templates(directory="templates")


class WorkerPayload(BaseModel):
    worker_code: str
    worker_name: str
    department: Optional[str] = None
    process_codes: List[str] = []
    is_active: str = "Y"
    note: Optional[str] = None


def _can_read(user: Any) -> bool:
    if not user:
        return False
    if str(getattr(user, "role", "")).strip().upper() == "ADMIN":
        return True
    return check_permission(user, "basic_info", "READ")


def _serialize_worker(worker: WorkerMaster):
    return {
        "id": worker.id,
        "worker_code": worker.worker_code,
        "worker_name": worker.worker_name,
        "department": worker.department or "",
        "process_codes": [x.process_code for x in worker.processes],
        "is_active": worker.is_active,
        "note": worker.note or "",
    }


def _validate_processes(db: Session, process_codes: List[str]) -> List[str]:
    codes = list(dict.fromkeys([str(x).strip() for x in process_codes if str(x).strip()]))
    if not codes:
        raise HTTPException(status_code=400, detail="생산 가능한 공정을 최소 1개 이상 선택해 주세요.")
    valid = {
        x.process_code
        for x in db.query(ProcessModel)
        .filter(ProcessModel.process_code.in_(codes), ProcessModel.is_active == "Y")
        .all()
    }
    invalid = [x for x in codes if x not in valid]
    if invalid:
        raise HTTPException(status_code=400, detail=f"사용할 수 없는 공정이 포함되어 있습니다: {', '.join(invalid)}")
    return codes


@router.get("/basic-info/workers", response_class=HTMLResponse)
def worker_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not _can_read(user):
        return RedirectResponse(url="/shipping", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="worker_master.html",
        context={"request": request, "user": user},
    )


@router.get("/api/basic-info/workers/processes")
def worker_process_options(request: Request, db: Session = Depends(get_db)):
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


@router.get("/api/basic-info/workers")
def list_workers(
    request: Request,
    process_code: Optional[str] = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    query = db.query(WorkerMaster)
    if active_only:
        query = query.filter(WorkerMaster.is_active == "Y")
    if process_code:
        query = query.join(WorkerProcess).filter(WorkerProcess.process_code == process_code.strip())
    rows = query.order_by(WorkerMaster.worker_code.asc()).all()
    return [_serialize_worker(x) for x in rows]


@router.post("/api/basic-info/workers")
def create_worker(
    payload: WorkerPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    code = payload.worker_code.strip()
    name = payload.worker_name.strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="작업자코드와 작업자명은 필수입니다.")
    if db.query(WorkerMaster.id).filter(WorkerMaster.worker_code == code).first():
        raise HTTPException(status_code=400, detail=f"이미 등록된 작업자코드입니다. ({code})")

    process_codes = _validate_processes(db, payload.process_codes)
    active = payload.is_active.strip().upper()
    if active not in {"Y", "N"}:
        raise HTTPException(status_code=400, detail="사용여부 값이 올바르지 않습니다.")

    worker = WorkerMaster(
        worker_code=code,
        worker_name=name,
        department=(payload.department or "").strip() or None,
        is_active=active,
        note=(payload.note or "").strip() or None,
    )
    worker.processes = [WorkerProcess(process_code=x) for x in process_codes]
    db.add(worker)
    db.commit()
    db.refresh(worker)
    return {"status": "success", "id": worker.id, "message": "작업자가 등록되었습니다."}


@router.put("/api/basic-info/workers/{worker_id}")
def update_worker(
    worker_id: int,
    payload: WorkerPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user_optional(request, db)
    if not _can_read(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    worker = db.query(WorkerMaster).filter(WorkerMaster.id == worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="작업자를 찾을 수 없습니다.")

    code = payload.worker_code.strip()
    name = payload.worker_name.strip()
    if not code or not name:
        raise HTTPException(status_code=400, detail="작업자코드와 작업자명은 필수입니다.")
    duplicate = (
        db.query(WorkerMaster.id)
        .filter(WorkerMaster.worker_code == code, WorkerMaster.id != worker_id)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail=f"이미 등록된 작업자코드입니다. ({code})")

    process_codes = _validate_processes(db, payload.process_codes)
    active = payload.is_active.strip().upper()
    if active not in {"Y", "N"}:
        raise HTTPException(status_code=400, detail="사용여부 값이 올바르지 않습니다.")

    worker.worker_code = code
    worker.worker_name = name
    worker.department = (payload.department or "").strip() or None
    worker.is_active = active
    worker.note = (payload.note or "").strip() or None
    worker.processes.clear()
    worker.processes.extend([WorkerProcess(process_code=x) for x in process_codes])
    db.commit()
    return {"status": "success", "message": "작업자 정보가 수정되었습니다."}
