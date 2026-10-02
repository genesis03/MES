from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import get_current_user, get_current_user_optional
from models.models import ItemMasterModel
from models.process_flow import ProcessFlowRevision, ProcessFlowStep, ProcessFlowStepKey
from services.document_service import actor_name, lock_item
from services.fmea_service import commit_fmea
from services.process_flow_service import (
    FLOW_MENU_PATH, flow_dict, flow_steps, get_flow, has_flow_access, lock_flow, require_flow_access,
)

router = APIRouter(tags=["Process Flow"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class StepPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    id: int | None = Field(default=None, gt=0)
    step_no: str = Field(min_length=1, max_length=50)
    step_name: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class CreatePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    item_id: int = Field(gt=0)
    revision_code: str = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=8000)
    steps: list[StepPayload] = Field(default_factory=list, max_length=500)


class SavePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    version: int = Field(gt=0)
    note: str = Field(default="", max_length=8000)
    steps: list[StepPayload] = Field(default_factory=list, max_length=500)


class VersionPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    version: int = Field(gt=0)


class RevisePayload(VersionPayload):
    revision_code: str = Field(min_length=1, max_length=50)
    change_reason: str = Field(min_length=1, max_length=4000)


class RetirePayload(VersionPayload):
    reason: str = Field(min_length=1, max_length=4000)


def _save_steps(db, revision, payload_steps, user):
    existing = {x.id: x for x in flow_steps(db, revision.id)}
    ids = [x.id for x in payload_steps if x.id]
    if len(ids) != len(set(ids)) or any(x not in existing for x in ids):
        raise HTTPException(422, "다른 개정의 공정이 포함되었거나 행이 중복되었습니다.")
    numbers = [x.step_no.casefold() for x in payload_steps]
    if len(numbers) != len(set(numbers)):
        raise HTTPException(422, "같은 공정흐름도 안에서 공정번호가 중복될 수 없습니다.")
    for index, payload in enumerate(payload_steps, 1):
        if payload.id:
            row = existing[payload.id]
        else:
            key = ProcessFlowStepKey(item_id=revision.item_id)
            db.add(key)
            db.flush()
            row = ProcessFlowStep(revision_id=revision.id, step_key_id=key.id)
        row.step_no, row.step_name, row.note = payload.step_no, payload.step_name, payload.note
        row.sort_order = index
        db.add(row)
    for row_id, row in existing.items():
        if row_id not in ids:
            row.retired_at, row.retired_by_id = datetime.now(), user.id


@router.get(FLOW_MENU_PATH, response_class=HTMLResponse)
def flow_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_flow_access(user)
    return templates.TemplateResponse(request=request, name="standard_documents/process_flow.html",
        context={"user": user, "can_write_flow": has_flow_access(user, "WRITE")})


@router.get("/api/process-flows/options")
def options(db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user)
    return [{"id": x.id, "part_no": x.part_no, "part_name": x.part_name, "is_active": x.is_active}
            for x in db.scalars(select(ItemMasterModel).order_by(ItemMasterModel.part_no))]


@router.get("/api/process-flows")
def list_flows(keyword: str = "", db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user)
    query = select(ProcessFlowRevision).join(ItemMasterModel, ItemMasterModel.id == ProcessFlowRevision.item_id)
    if keyword.strip():
        value = f"%{keyword.strip()}%"
        query = query.where(ItemMasterModel.part_no.ilike(value) | ItemMasterModel.part_name.ilike(value))
    return [flow_dict(db, x, False) for x in db.scalars(query.order_by(
        ItemMasterModel.part_no, ProcessFlowRevision.sequence.desc()))]


@router.get("/api/process-flows/{revision_id}")
def detail(revision_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user)
    return flow_dict(db, get_flow(db, revision_id))


@router.post("/api/process-flows", status_code=201)
def create(payload: CreatePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    item = lock_item(db, payload.item_id)
    if db.scalar(select(ProcessFlowRevision.id).where(ProcessFlowRevision.item_id == item.id)):
        raise HTTPException(409, "공정흐름도 이력이 있습니다. 기존 문서에서 개정 등록해 주세요.")
    row = ProcessFlowRevision(item_id=item.id, revision_code=payload.revision_code, sequence=1,
        part_no_snapshot=item.part_no, part_name_snapshot=item.part_name, note=payload.note,
        created_by_id=user.id, created_by=actor_name(user))
    db.add(row)
    db.flush()
    _save_steps(db, row, payload.steps, user)
    commit_fmea(db)
    return flow_dict(db, row)


@router.put("/api/process-flows/{revision_id}")
def save(revision_id: int, payload: SavePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    item, row = lock_flow(db, revision_id, payload.version)
    if row.status != "DRAFT":
        raise HTTPException(409, "적용된 공정흐름도는 개정 등록으로 변경해 주세요.")
    row.note, row.updated_at = payload.note, datetime.now()
    row.part_no_snapshot, row.part_name_snapshot = item.part_no, item.part_name
    _save_steps(db, row, payload.steps, user)
    row.version += 1
    commit_fmea(db)
    return flow_dict(db, row)


@router.post("/api/process-flows/{revision_id}/revise", status_code=201)
def revise(revision_id: int, payload: RevisePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    item, previous = lock_flow(db, revision_id, payload.version)
    if previous.status == "DRAFT":
        raise HTTPException(409, "초안은 바로 수정해 주세요.")
    revisions = db.scalars(select(ProcessFlowRevision).where(ProcessFlowRevision.item_id == item.id)).all()
    if any(x.status == "DRAFT" for x in revisions):
        raise HTTPException(409, "작성 중인 초안이 있습니다. 해당 초안을 선택해 주세요.")
    if any(x.revision_code.casefold() == payload.revision_code.casefold() for x in revisions):
        raise HTTPException(409, "이미 사용한 개정번호입니다.")
    row = ProcessFlowRevision(item_id=item.id, revision_code=payload.revision_code,
        sequence=max(x.sequence for x in revisions) + 1, previous_revision_id=previous.id,
        part_no_snapshot=item.part_no, part_name_snapshot=item.part_name, note=previous.note,
        change_reason=payload.change_reason, created_by_id=user.id, created_by=actor_name(user))
    db.add(row)
    db.flush()
    for old in flow_steps(db, previous.id):
        db.add(ProcessFlowStep(revision_id=row.id, step_key_id=old.step_key_id, sort_order=old.sort_order,
                               step_no=old.step_no, step_name=old.step_name, note=old.note))
    commit_fmea(db)
    return flow_dict(db, row)


@router.post("/api/process-flows/{revision_id}/activate")
def activate(revision_id: int, payload: VersionPayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    item, row = lock_flow(db, revision_id, payload.version)
    if row.status != "DRAFT":
        raise HTTPException(409, "초안만 현재 사용으로 적용할 수 있습니다.")
    steps = flow_steps(db, row.id)
    if not steps:
        raise HTTPException(422, "공정을 1개 이상 등록해 주세요.")
    numbers = [x.step_no.casefold() for x in steps]
    if len(numbers) != len(set(numbers)) or any(not x.step_no.strip() or not x.step_name.strip() for x in steps):
        raise HTTPException(422, "공정번호·공정명을 확인해 주세요.")
    now = datetime.now()
    for old in db.scalars(select(ProcessFlowRevision).where(
        ProcessFlowRevision.item_id == item.id, ProcessFlowRevision.status == "CURRENT")):
        if old.sequence >= row.sequence:
            raise HTTPException(409, "현재 사용보다 이전 개정은 적용할 수 없습니다.")
        old.status, old.superseded_at = "SUPERSEDED", now
        old.version += 1
    db.flush()
    row.status, row.activated_at, row.activated_by_id = "CURRENT", now, user.id
    row.part_no_snapshot, row.part_name_snapshot = item.part_no, item.part_name
    row.version += 1
    commit_fmea(db)
    return flow_dict(db, row)


@router.post("/api/process-flows/{revision_id}/retire")
def retire(revision_id: int, payload: RetirePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    _, row = lock_flow(db, revision_id, payload.version, require_active=False)
    if row.status == "RETIRED":
        raise HTTPException(409, "이미 폐기된 개정입니다.")
    row.status, row.retired_at, row.retired_by_id = "RETIRED", datetime.now(), user.id
    row.retire_reason = payload.reason
    row.version += 1
    commit_fmea(db)
    return flow_dict(db, row)
