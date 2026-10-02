import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from services.revision_number_service import normalize_revision_code, revision_key
from core.security import get_current_user, get_current_user_optional
from models.audit_log import AuditLogModel
from models.fmea import FmeaRow
from models.models import ItemMasterModel
from models.process_flow import ProcessFlowRevision, ProcessFlowStep, ProcessFlowStepKey
from services.document_service import actor_name, lock_item
from services.standard_document_item_service import require_finished_item, selectable_finished_items
from services.fmea_service import commit_fmea, preserve_linked_fmea_flows
from services.process_flow_symbols import flow_symbol_options, resolve_flow_symbol, SUPPORTED_SHAPES
from services.process_flow_service import (
    FLOW_MENU_PATH, flow_dict, step_dict, flow_steps, get_flow, has_flow_access, lock_flow, require_flow_access,
)

router = APIRouter(tags=["Process Flow"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class StepPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    id: int | None = Field(default=None, gt=0)
    step_no: str = Field(min_length=1, max_length=50)
    step_name: str = Field(min_length=1, max_length=200)
    symbol_code: str = Field(default="", max_length=50)
    note: str = Field(default="", max_length=4000)


class CreatePayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    item_id: int = Field(gt=0)
    revision_code: str = Field(min_length=1, max_length=50)
    _normalize_revision = field_validator("revision_code", mode="before")(normalize_revision_code)
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
    _normalize_revision = field_validator("revision_code", mode="before")(normalize_revision_code)
    change_reason: str = Field(min_length=1, max_length=4000)


class CorrectionStepPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    id: int = Field(gt=0)
    step_name: str = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=4000)


class CorrectionPayload(VersionPayload):
    reason: str = Field(min_length=1, max_length=4000)
    note: str = Field(default="", max_length=8000)
    steps: list[CorrectionStepPayload] = Field(min_length=1, max_length=500)


class EditPayload(SavePayload):
    reason: str = Field(min_length=1, max_length=4000)
    steps: list[StepPayload] = Field(min_length=1, max_length=500)


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
        # 같은 기호의 저장된 명칭/도형은 공통코드 이름 변경으로 덮어쓰지 않습니다.
        # 구형 화면이 기호 필드를 보내지 않으면 이미 저장된 기호를 지우지 않습니다.
        if "symbol_code" in payload.model_fields_set:
            if payload.symbol_code:
                if (payload.symbol_code != row.symbol_code or not row.symbol_name_snapshot
                        or row.symbol_shape_snapshot not in SUPPORTED_SHAPES):
                    symbol = resolve_flow_symbol(db, payload.symbol_code)
                    row.symbol_code = symbol["code"]
                    row.symbol_name_snapshot, row.symbol_shape_snapshot = symbol["name"], symbol["shape"]
            else:
                row.symbol_code = row.symbol_name_snapshot = row.symbol_shape_snapshot = None
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
            for x in selectable_finished_items(db)]


@router.get("/api/process-flows/symbols")
def symbols(db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user)
    return flow_symbol_options(db)


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
    require_finished_item(db, item)
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


@router.get("/api/process-flows/{revision_id}/corrections")
def correction_history(revision_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user)
    get_flow(db, revision_id)
    logs = db.scalars(select(AuditLogModel).where(
        AuditLogModel.table_name == "process_flow_revisions",
        AuditLogModel.record_id == str(revision_id), AuditLogModel.action.in_(("CORRECT", "AMEND"))
    ).order_by(AuditLogModel.id.desc())).all()
    return [{"id": x.id, "corrected_at": x.event_at, "corrected_by": x.username,
             **json.loads(x.after_json)} for x in logs]


@router.post("/api/process-flows/{revision_id}/edit")
def edit_current(revision_id: int, payload: EditPayload, request: Request,
                 db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    _, revision = lock_flow(db, revision_id, payload.version)
    if revision.status != "CURRENT":
        raise HTTPException(409, "현재 사용 문서에서 수정해 주세요. 초안은 초안 저장을 사용합니다.")
    for index, entry in enumerate(payload.steps, 1):
        if not entry.symbol_code:
            raise HTTPException(422, f"{index}번째 공정의 기호를 선택해 주세요.")
        resolve_flow_symbol(db, entry.symbol_code)
    before = flow_dict(db, revision)
    preserve_linked_fmea_flows(db, revision)
    _save_steps(db, revision, payload.steps, user)
    revision.note, revision.updated_at = payload.note, datetime.now()
    db.flush()
    after_flow = flow_dict(db, revision)
    old_steps = {x["id"]: x for x in before["steps"]}
    new_steps = {x["id"]: x for x in after_flow["steps"]}
    changes = []
    if before["note"] != after_flow["note"]:
        changes.append({"step_no": "", "field": "문서 비고", "before": before["note"], "after": after_flow["note"]})
    labels = {"step_no": "공정번호", "step_name": "공정명", "symbol_code": "기호 코드",
              "symbol_name": "기호 명칭", "symbol_shape": "기호 도형", "sort_order": "공정순서", "note": "공정 비고"}
    for step_id, step in new_steps.items():
        old = old_steps.get(step_id)
        if old is None:
            changes.append({"step_no": step["step_no"], "field": "공정 추가", "before": "",
                            "after": json.dumps(step, ensure_ascii=False)})
            continue
        for field, label in labels.items():
            if old[field] != step[field]:
                changes.append({"step_no": step["step_no"], "field": label,
                                "before": str(old[field]), "after": str(step[field])})
    for step_id, step in old_steps.items():
        if step_id not in new_steps:
            changes.append({"step_no": step["step_no"], "field": "공정 삭제 · 이력 보존",
                            "before": json.dumps(step, ensure_ascii=False), "after": ""})
    if not changes:
        db.rollback()
        raise HTTPException(422, "수정된 내용이 없습니다.")
    revision.version += 1
    after_flow["version"] = revision.version
    after = {"revision_code": revision.revision_code, "reason": payload.reason,
             "version": revision.version, "changes": changes, "flow": after_flow}
    db.add(AuditLogModel(event_at=revision.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        user_id=user.id, username=actor_name(user), action="AMEND",
        table_name="process_flow_revisions", record_id=str(revision.id),
        document_no=revision.revision_code, menu_path=FLOW_MENU_PATH,
        request_path=request.url.path, request_method=request.method,
        ip_address=request.client.host if request.client else None,
        changed_fields=",".join(sorted({x["field"] for x in changes})),
        before_json=json.dumps(before, ensure_ascii=False), after_json=json.dumps(after, ensure_ascii=False)))
    commit_fmea(db)
    return flow_dict(db, revision)


@router.post("/api/process-flows/{revision_id}/correct")
def correct(revision_id: int, payload: CorrectionPayload, request: Request,
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    _, revision = lock_flow(db, revision_id, payload.version)
    if revision.status != "CURRENT":
        raise HTTPException(409, "현재 사용 문서만 오타 정정할 수 있습니다. 초안은 바로 수정해 주세요.")
    steps = flow_steps(db, revision.id)
    if [x.id for x in payload.steps] != [x.id for x in steps]:
        raise HTTPException(422, "오타 정정에서는 공정 추가·삭제·순서 변경을 할 수 없습니다.")
    changes = []
    if (revision.note or "") != payload.note:
        changes.append({"step_no": "", "field": "문서 비고", "before": revision.note or "", "after": payload.note})
    for step, entry in zip(steps, payload.steps):
        for field, label in (("step_name", "공정명"), ("note", "공정 비고")):
            before, after = getattr(step, field) or "", getattr(entry, field)
            if before != after:
                changes.append({"step_no": step.step_no, "field": label, "before": before, "after": after})
    if not changes:
        raise HTTPException(422, "정정된 내용이 없습니다.")
    preserve_linked_fmea_flows(db, revision)
    # 기존/구형 FMEA도 수정 전 명칭을 보존합니다. 같은 품목 잠금 안에서 원자적으로 처리합니다.
    ids = [x.id for x in steps]
    names = {x.id: x.step_name for x in steps}
    for linked in db.scalars(select(FmeaRow).where(
        FmeaRow.flow_step_id.in_(ids), FmeaRow.flow_step_name_snapshot.is_(None))):
        linked.flow_step_name_snapshot = names[linked.flow_step_id]
    before = {"revision_code": revision.revision_code, "version": revision.version,
              "note": revision.note or "", "steps": [
                  {"id": x.id, "step_no": x.step_no, "step_name": x.step_name, "note": x.note or ""} for x in steps]}
    revision.note, revision.updated_at = payload.note, datetime.now()
    for step, entry in zip(steps, payload.steps):
        step.step_name, step.note = entry.step_name, entry.note
    revision.version += 1
    after = {"revision_code": revision.revision_code, "reason": payload.reason,
             "version": revision.version, "changes": changes}
    db.add(AuditLogModel(event_at=revision.updated_at.strftime("%Y-%m-%d %H:%M:%S.%f"),
        user_id=user.id, username=actor_name(user), action="CORRECT",
        table_name="process_flow_revisions", record_id=str(revision.id),
        document_no=revision.revision_code, menu_path=FLOW_MENU_PATH,
        request_path=request.url.path, request_method=request.method,
        ip_address=request.client.host if request.client else None,
        changed_fields=",".join(sorted({x["field"] for x in changes})),
        before_json=json.dumps(before, ensure_ascii=False), after_json=json.dumps(after, ensure_ascii=False)))
    commit_fmea(db)
    return flow_dict(db, revision)


@router.post("/api/process-flows/{revision_id}/revise", status_code=201)
def revise(revision_id: int, payload: RevisePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_flow_access(user, "WRITE")
    item, previous = lock_flow(db, revision_id, payload.version)
    if previous.status == "DRAFT":
        raise HTTPException(409, "초안은 바로 수정해 주세요.")
    revisions = db.scalars(select(ProcessFlowRevision).where(ProcessFlowRevision.item_id == item.id)).all()
    if any(x.status == "DRAFT" for x in revisions):
        raise HTTPException(409, "작성 중인 초안이 있습니다. 해당 초안을 선택해 주세요.")
    if any(revision_key(x.revision_code) == revision_key(payload.revision_code) for x in revisions):
        raise HTTPException(409, "이미 사용한 개정번호입니다.")
    row = ProcessFlowRevision(item_id=item.id, revision_code=payload.revision_code,
        sequence=max(x.sequence for x in revisions) + 1, previous_revision_id=previous.id,
        part_no_snapshot=item.part_no, part_name_snapshot=item.part_name, note=previous.note,
        change_reason=payload.change_reason, created_by_id=user.id, created_by=actor_name(user))
    db.add(row)
    db.flush()
    for old in flow_steps(db, previous.id):
        db.add(ProcessFlowStep(revision_id=row.id, step_key_id=old.step_key_id, sort_order=old.sort_order,
                               step_no=old.step_no, step_name=old.step_name, note=old.note,
                               symbol_code=old.symbol_code, symbol_name_snapshot=old.symbol_name_snapshot,
                               symbol_shape_snapshot=old.symbol_shape_snapshot))
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
    for index, step in enumerate(steps, 1):
        if not step.symbol_code or not step.symbol_name_snapshot or step.symbol_shape_snapshot not in SUPPORTED_SHAPES:
            raise HTTPException(422, f"{index}번째 공정의 기호를 선택해 주세요.")
        resolve_flow_symbol(db, step.symbol_code)
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
