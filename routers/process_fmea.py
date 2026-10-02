import json
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import get_current_user, get_current_user_optional
from models.document import ItemRevision
from models.fmea import FmeaDocument, FmeaRevision, FmeaRow
from models.item_identity import ItemPartNoHistory
from models.models import ItemMasterModel
from models.process_flow import ProcessFlowRevision
from services.process_flow_service import flow_dict, flow_steps, usable_flow
from services.document_service import actor_name, lock_item
from services.standard_document_item_service import require_finished_item, selectable_finished_items
from services.fmea_service import (
    FMEA_MENU_PATH, HEADER_FIELDS, ROW_FIELDS, commit_fmea, get_revision, has_fmea_access,
    lock_fmea_revision, require_fmea_access, revision_dict,
)

router = APIRouter(tags=["Process FMEA"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class HeaderPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    company: str = Field(default="", max_length=200)
    model_year: str = Field(default="", max_length=100)
    team: str = Field(default="", max_length=200)
    prepared_by: str = Field(min_length=1, max_length=100)
    date_prepared: date
    basis_item_revision_id: int | None = Field(default=None, gt=0)
    note: str = Field(default="", max_length=8000)
    flow_revision_id: int | None = Field(default=None, gt=0)
    process_owner: str = Field(default="", max_length=100)
    completion_due_date: date | None = None
    mass_production_date: date | None = None


class RowPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    id: int | None = Field(default=None, gt=0)
    flow_step_id: int | None = Field(default=None, gt=0)
    action_not_applicable: bool = False
    process_code: str | None = Field(default=None, max_length=100)
    function_text: str = Field(default="", max_length=4000)
    failure_mode: str = Field(default="", max_length=4000)
    effects: str = Field(default="", max_length=4000)
    severity: int | None = Field(default=None, ge=1, le=10, strict=True)
    classification: str = Field(default="", max_length=50)
    causes: str = Field(default="", max_length=4000)
    occurrence: int | None = Field(default=None, ge=1, le=10, strict=True)
    prevention_controls: str = Field(default="", max_length=4000)
    detection_controls: str = Field(default="", max_length=4000)
    detection: int | None = Field(default=None, ge=1, le=10, strict=True)
    recommended_actions: str = Field(default="", max_length=4000)
    responsibility: str = Field(default="", max_length=100)
    target_date: date | None = None
    actions_taken: str = Field(default="", max_length=4000)
    completion_date: date | None = None
    new_severity: int | None = Field(default=None, ge=1, le=10, strict=True)
    new_occurrence: int | None = Field(default=None, ge=1, le=10, strict=True)
    new_detection: int | None = Field(default=None, ge=1, le=10, strict=True)
    note: str = Field(default="", max_length=4000)


class CreatePayload(HeaderPayload):
    item_id: int = Field(gt=0)
    document_no: str = Field(min_length=1, max_length=100)
    revision_code: str = Field(min_length=1, max_length=50)
    rows: list[RowPayload] = Field(default_factory=list, max_length=500)


class SavePayload(HeaderPayload):
    version: int = Field(gt=0)
    rows: list[RowPayload] = Field(default_factory=list, max_length=500)


class VersionPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    version: int = Field(gt=0)


class RevisePayload(VersionPayload):
    revision_code: str = Field(min_length=1, max_length=50)
    change_reason: str = Field(min_length=1, max_length=4000)


class RetirePayload(VersionPayload):
    reason: str = Field(min_length=1, max_length=4000)


def _header(db, revision, item, payload):
    for field in HEADER_FIELDS:
        setattr(revision, field, getattr(payload, field))
    basis = db.get(ItemRevision, payload.basis_item_revision_id) if payload.basis_item_revision_id else None
    if payload.basis_item_revision_id and (not basis or basis.item_id != item.id or basis.status == "RETIRED"):
        raise HTTPException(422, "같은 품목의 폐기되지 않은 기준 도면 Revision을 선택해 주세요.")
    revision.basis_item_revision_id = basis.id if basis else None
    revision.basis_revision_snapshot = basis.revision_code if basis else None
    if payload.flow_revision_id:
        usable_flow(db, item.id, payload.flow_revision_id)
    revision.flow_revision_id = payload.flow_revision_id
    revision.vehicle_model_snapshot = item.vehicle_model
    revision.part_no_snapshot, revision.part_name_snapshot = item.part_no, item.part_name


def _save_rows(db, revision, payload_rows, user):
    existing = {row.id: row for row in db.scalars(select(FmeaRow).where(
        FmeaRow.revision_id == revision.id, FmeaRow.retired_at.is_(None)))}
    ids = [row.id for row in payload_rows if row.id is not None]
    if len(ids) != len(set(ids)) or any(row_id not in existing for row_id in ids):
        raise HTTPException(422, "분석행이 중복되었거나 다른 개정의 행이 포함되어 있습니다.")
    steps = {x.id: x for x in flow_steps(db, revision.flow_revision_id)} if revision.flow_revision_id else {}
    for index, payload in enumerate(payload_rows, 1):
        if payload.flow_step_id and payload.flow_step_id not in steps:
            raise HTTPException(422, f"{index}행: 선택한 공정흐름도의 공정으로 연결해 주세요. 기존 행을 임의로 변경하지 않습니다.")
        row = existing[payload.id] if payload.id else FmeaRow(revision_id=revision.id)
        old_step_id = row.flow_step_id
        if payload.flow_step_id and (old_step_id != payload.flow_step_id or row.flow_step_name_snapshot is None):
            row.flow_step_name_snapshot = steps[payload.flow_step_id].step_name
        elif not payload.flow_step_id:
            row.flow_step_name_snapshot = None
        for field in ROW_FIELDS:
            if field != "process_code":
                setattr(row, field, getattr(payload, field))
        if payload.flow_step_id:
            row.process_code = None
        if row.action_not_applicable:
            fields = ("recommended_actions", "responsibility", "target_date", "actions_taken",
                      "completion_date", "new_severity", "new_occurrence", "new_detection")
            if any(getattr(row, field) not in (None, "") for field in fields):
                raise HTTPException(422, f"{index}행: 조치 해당없음과 조치 내용을 동시에 저장할 수 없습니다.")
        row.sort_order = index
        db.add(row)
    # 초안에서 제외한 저장 행도 삭제하지 않고 이력으로 남깁니다.
    for row_id, row in existing.items():
        if row_id not in ids:
            row.retired_at, row.retired_by_id = datetime.now(), user.id


@router.get(FMEA_MENU_PATH, response_class=HTMLResponse)
def fmea_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_fmea_access(user)
    return templates.TemplateResponse(request=request, name="standard_documents/process_fmea.html",
        context={"user": user, "can_write_fmea": has_fmea_access(user, "WRITE"),
                 "default_prepared_by": actor_name(user), "default_team": user.department or "",
                 "today": date.today().isoformat()})


@router.get("/api/process-fmea/options")
def options(db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    return {
        "items": [{"id": x.id, "part_no": x.part_no, "part_name": x.part_name, "is_active": x.is_active, "vehicle_model": x.vehicle_model or ""}
                  for x in selectable_finished_items(db)],

    }


@router.get("/api/process-fmea/items/{item_id}/flows")
def item_flows(item_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    return [flow_dict(db, x) for x in db.scalars(select(ProcessFlowRevision).where(
        ProcessFlowRevision.item_id == item_id, ProcessFlowRevision.status.in_(("CURRENT", "SUPERSEDED"))
    ).order_by(ProcessFlowRevision.sequence.desc()))]


@router.get("/api/process-fmea/items/{item_id}/drawing-revisions")
def drawing_revisions(item_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    return [{"id": x.id, "revision_code": x.revision_code, "status": x.status}
            for x in db.scalars(select(ItemRevision).where(ItemRevision.item_id == item_id,
                ItemRevision.status != "RETIRED").order_by(ItemRevision.sequence.desc()))]


@router.get("/api/process-fmea/documents")
def list_documents(keyword: str = "", status: str = "", db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    if status and status not in {"DRAFT", "CURRENT", "SUPERSEDED", "RETIRED"}:
        raise HTTPException(422, "조회 상태가 올바르지 않습니다.")
    query = select(FmeaDocument, ItemMasterModel).join(ItemMasterModel, ItemMasterModel.id == FmeaDocument.item_id)
    if keyword.strip():
        value = f"%{keyword.strip()}%"
        history = select(ItemPartNoHistory.item_id).where(
            ItemPartNoHistory.old_part_no.ilike(value) | ItemPartNoHistory.new_part_no.ilike(value))
        query = query.where(ItemMasterModel.part_no.ilike(value) | ItemMasterModel.part_name.ilike(value)
                            | FmeaDocument.document_no.ilike(value) | FmeaDocument.item_id.in_(history))
    results = []
    for document, item in db.execute(query.order_by(ItemMasterModel.part_no, FmeaDocument.id)):
        revisions = db.scalars(select(FmeaRevision).where(FmeaRevision.document_id == document.id)
                               .order_by(FmeaRevision.sequence.desc())).all()
        if not revisions:
            continue
        matches = [x for x in revisions if not status or x.status == status]
        if not matches:
            continue
        chosen = matches[0]
        current = next((x for x in revisions if x.status == "CURRENT"), None)
        results.append({
            "id": document.id, "item_id": item.id, "part_no": item.part_no, "part_name": item.part_name,
            "document_no": document.document_no, "revision_id": chosen.id, "revision_code": chosen.revision_code,
            "status": chosen.status, "current_revision": current.revision_code if current else "",
            "prepared_by": chosen.prepared_by, "created_at": chosen.created_at.strftime("%Y-%m-%d %H:%M"),
        })
    return results


@router.get("/api/process-fmea/documents/{document_id}/revisions")
def list_revisions(document_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    if not db.get(FmeaDocument, document_id):
        raise HTTPException(404, "공정 FMEA를 찾을 수 없습니다.")
    return [revision_dict(db, x, False) for x in db.scalars(select(FmeaRevision).where(
        FmeaRevision.document_id == document_id).order_by(FmeaRevision.sequence.desc()))]


@router.get("/api/process-fmea/revisions/{revision_id}")
def detail(revision_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    return revision_dict(db, get_revision(db, revision_id))


@router.post("/api/process-fmea/documents", status_code=201)
def create_document(payload: CreatePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user, "WRITE")
    item = lock_item(db, payload.item_id)
    require_finished_item(db, item)
    duplicate = db.scalar(select(FmeaDocument.id).where(FmeaDocument.item_id == item.id,
        func.lower(FmeaDocument.document_no) == payload.document_no.lower()))
    if duplicate:
        raise HTTPException(409, "동일 품목에 같은 FMEA 번호가 있습니다. 기존 문서에서 개정 등록해 주세요.")
    document = FmeaDocument(item_id=item.id, document_no=payload.document_no,
                            created_by_id=user.id, created_by=actor_name(user))
    db.add(document)
    db.flush()
    revision = FmeaRevision(document_id=document.id, revision_code=payload.revision_code, sequence=1,
                            created_by_id=user.id, created_by=actor_name(user))
    _header(db, revision, item, payload)
    db.add(revision)
    db.flush()
    _save_rows(db, revision, payload.rows, user)
    commit_fmea(db)
    return revision_dict(db, revision)


@router.put("/api/process-fmea/revisions/{revision_id}")
def save_draft(revision_id: int, payload: SavePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user, "WRITE")
    item, _, revision = lock_fmea_revision(db, revision_id, payload.version)
    if revision.status != "DRAFT":
        raise HTTPException(409, "초안만 수정할 수 있습니다. 적용된 문서는 개정 등록해 주세요.")
    _header(db, revision, item, payload)
    _save_rows(db, revision, payload.rows, user)
    revision.version += 1
    revision.updated_by_id, revision.updated_at = user.id, datetime.now()
    commit_fmea(db)
    return revision_dict(db, revision)


@router.post("/api/process-fmea/revisions/{revision_id}/revise", status_code=201)
def revise(revision_id: int, payload: RevisePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user, "WRITE")
    item, document, previous = lock_fmea_revision(db, revision_id, payload.version)
    if previous.status not in {"CURRENT", "SUPERSEDED"}:
        raise HTTPException(409, "현재 사용 또는 구버전에서 개정 등록해 주세요.")
    revisions = db.scalars(select(FmeaRevision).where(FmeaRevision.document_id == document.id)).all()
    if any(x.status == "DRAFT" for x in revisions):
        raise HTTPException(409, "작성 중인 초안이 있습니다. 해당 초안을 선택해 주세요.")
    if any(x.revision_code.lower() == payload.revision_code.lower() for x in revisions):
        raise HTTPException(409, "이미 사용한 개정번호입니다.")
    row = FmeaRevision(
        document_id=document.id, revision_code=payload.revision_code,
        sequence=max(x.sequence for x in revisions) + 1, previous_revision_id=previous.id,
        change_reason=payload.change_reason, basis_item_revision_id=previous.basis_item_revision_id,
        basis_revision_snapshot=previous.basis_revision_snapshot, part_no_snapshot=item.part_no,
        part_name_snapshot=item.part_name, created_by_id=user.id, created_by=actor_name(user),
        flow_revision_id=previous.flow_revision_id, vehicle_model_snapshot=item.vehicle_model, diff_tracking=True,
    )
    for field in HEADER_FIELDS:
        setattr(row, field, getattr(previous, field))
    row.prepared_by, row.date_prepared = actor_name(user), date.today()
    db.add(row)
    db.flush()
    for old in db.scalars(select(FmeaRow).where(FmeaRow.revision_id == previous.id, FmeaRow.retired_at.is_(None))):
        copied = FmeaRow(revision_id=row.id, sort_order=old.sort_order, previous_row_id=old.id,
                        process_code_snapshot=old.process_code_snapshot, process_name_snapshot=old.process_name_snapshot,
                        flow_step_name_snapshot=old.flow_step_name_snapshot)
        for field in ROW_FIELDS:
            setattr(copied, field, getattr(old, field))
        db.add(copied)
    commit_fmea(db)
    return revision_dict(db, row)


@router.post("/api/process-fmea/revisions/{revision_id}/activate")
def activate(revision_id: int, payload: VersionPayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user, "WRITE")
    item, document, revision = lock_fmea_revision(db, revision_id, payload.version)
    if revision.status != "DRAFT":
        raise HTTPException(409, "초안만 현재 사용으로 적용할 수 있습니다.")
    if revision.basis_item_revision_id:
        basis = db.get(ItemRevision, revision.basis_item_revision_id)
        if not basis or basis.item_id != item.id or basis.status == "RETIRED":
            raise HTTPException(422, "기준 도면 Revision이 폐기되었습니다. 초안에서 기준을 다시 선택해 주세요.")
    rows = db.scalars(select(FmeaRow).where(FmeaRow.revision_id == revision.id,
                      FmeaRow.retired_at.is_(None)).order_by(FmeaRow.sort_order)).all()
    if not rows:
        raise HTTPException(422, "분석행을 1개 이상 작성해 주세요.")
    if not revision.flow_revision_id:
        raise HTTPException(422, "공정흐름도를 먼저 등록·적용한 뒤 FMEA에서 선택해 주세요.")
    flow = usable_flow(db, item.id, revision.flow_revision_id)
    if flow.status != "CURRENT":
        raise HTTPException(422, "현재 사용 공정흐름도와 일치하도록 초안의 기준을 변경해 주세요.")
    steps = {x.id: x for x in flow_steps(db, flow.id)}
    if not steps or {x.flow_step_id for x in rows} != set(steps):
        raise HTTPException(422, "공정흐름도의 모든 공정에 분석행을 연결해 주세요. 미연결·추가 공정은 적용할 수 없습니다.")
    rows.sort(key=lambda x: (steps[x.flow_step_id].sort_order, x.sort_order))
    for index, row in enumerate(rows, 1):
        for field in ("function_text", "failure_mode", "effects", "causes"):
            if not (getattr(row, field) or "").strip():
                raise HTTPException(422, f"{index}행: 기능·고장 형태·영향·원인을 모두 입력해 주세요.")
        if any(getattr(row, f) is None for f in ("severity", "occurrence", "detection")):
            raise HTTPException(422, f"{index}행: 심각도·발생도·검출도를 모두 평가해 주세요.")
        new_scores = [getattr(row, f) for f in ("new_severity", "new_occurrence", "new_detection")]
        if not row.action_not_applicable and any(x is not None for x in new_scores) and any(x is None for x in new_scores):
            raise HTTPException(422, f"{index}행: 조치 후 평가는 세 점수를 모두 입력하거나 모두 비워 주세요.")
        row.sort_order = index
        row.flow_step_name_snapshot = steps[row.flow_step_id].step_name
    now = datetime.now()
    for old in db.scalars(select(FmeaRevision).where(
        FmeaRevision.document_id == document.id, FmeaRevision.status == "CURRENT")):
        if old.sequence >= revision.sequence:
            raise HTTPException(409, "현재 사용보다 이전 순서의 개정은 적용할 수 없습니다.")
        old.status, old.superseded_at = "SUPERSEDED", now
        old.version += 1
    db.flush()
    revision.flow_snapshot_json = json.dumps(flow_dict(db, flow), ensure_ascii=False)
    revision.status, revision.activated_at = "CURRENT", now
    revision.activated_by_id, revision.activated_by = user.id, actor_name(user)
    revision.part_no_snapshot, revision.part_name_snapshot = item.part_no, item.part_name
    revision.version += 1
    commit_fmea(db)
    return revision_dict(db, revision)


@router.post("/api/process-fmea/revisions/{revision_id}/retire")
def retire(revision_id: int, payload: RetirePayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user, "WRITE")
    _, _, revision = lock_fmea_revision(db, revision_id, payload.version, require_active=False)
    if revision.status == "RETIRED":
        raise HTTPException(409, "이미 폐기된 개정입니다.")
    revision.status, revision.retired_at = "RETIRED", datetime.now()
    revision.retired_by_id, revision.retire_reason = user.id, payload.reason
    revision.version += 1
    commit_fmea(db)
    return revision_dict(db, revision)


@router.get("/api/process-fmea/revisions/{revision_id}/print", response_class=HTMLResponse)
def print_revision(revision_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_fmea_access(user)
    revision = get_revision(db, revision_id)
    labels = {"DRAFT": "초안", "CURRENT": "현재 사용", "SUPERSEDED": "구버전", "RETIRED": "폐기"}
    history = [revision_dict(db, x, False) for x in db.scalars(select(FmeaRevision).where(
        FmeaRevision.document_id == revision.document_id, FmeaRevision.sequence <= revision.sequence).order_by(FmeaRevision.sequence))]
    data = revision_dict(db, revision)
    groups = []
    for analysis in sorted(data["rows"], key=lambda x: (x["flow_sort_order"] or 1000000, x["sort_order"])):
        key = analysis["flow_step_id"] or ("legacy", analysis["id"])
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "rows": []})
        groups[-1]["rows"].append(analysis)
    return templates.TemplateResponse(request=request, name="standard_documents/fmea_print.html",
        context={"fmea": data, "status_label": labels[revision.status], "history": history,
                 "status_labels": labels, "groups": groups},
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/process-fmea/revisions/{revision_id}/changes")
def changes(revision_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_fmea_access(user)
    from services.fmea_change_service import fmea_changes
    return fmea_changes(db, get_revision(db, revision_id))
