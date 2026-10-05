"""입고/공정/최종 검사기준서 공통 화면 및 API."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.config import BASE_DIR
from core.database import get_db
from core.security import check_admin_permission, get_current_user, parse_user_permissions
from models.inspection_standard import InspectionStandard, InspectionStandardItem, InspectionStandardPrecheck
from models.models import ItemMasterModel, UserModel
from models.process_flow import ProcessFlowRevision, ProcessFlowStep, ProcessFlowStepKey
from services.revision_number_service import normalize_revision_code, revision_key
from services.standard_document_item_service import is_selectable_finished_item

router = APIRouter(tags=["Inspection Standards"])
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ROOT_PATH = "/standard-documents/inspection-standards"
TYPE_PATHS = {
    "INBOUND": ROOT_PATH + "/inbound",
    "PROCESS": ROOT_PATH + "/process",
    "FINAL": ROOT_PATH + "/final",
}
LEGACY_INBOUND_PATH = "/quality/inbound-standards"
DOC_NAMES = {"INBOUND": "입고검사 기준서", "PROCESS": "공정검사 기준서", "FINAL": "최종검사 기준서"}
VALID_TYPES = set(TYPE_PATHS)
VALID_STATUS = {"DRAFT", "CURRENT", "SUPERSEDED", "RETIRED"}


class PrecheckPayload(BaseModel):
    sort_order: int = Field(default=1, ge=1)
    check_item: str = Field(max_length=200)
    criteria: Optional[str] = None
    responsible: Optional[str] = Field(default=None, max_length=100)
    frequency: Optional[str] = Field(default=None, max_length=100)
    abnormal_action: Optional[str] = None
    note: Optional[str] = None


class InspectionItemPayload(BaseModel):
    sort_order: int = Field(default=1, ge=1)
    inspection_group_no: Optional[str] = Field(default=None, max_length=30)
    inspection_item_name: str = Field(max_length=200)
    detail_no: Optional[str] = Field(default=None, max_length=50)
    special_characteristic: Optional[str] = Field(default=None, max_length=50)
    inspection_tool: Optional[str] = Field(default=None, max_length=200)
    spec_text: Optional[str] = None
    nominal_value: Optional[float] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    unit: Optional[str] = Field(default=None, max_length=30)
    inspection_frequency: Optional[str] = Field(default=None, max_length=100)
    sample_qty_text: Optional[str] = Field(default=None, max_length=100)
    record_management: Optional[str] = Field(default=None, max_length=200)
    note: Optional[str] = None


class StandardPayload(BaseModel):
    document_type: str = Field(max_length=20)
    item_id: int = Field(gt=0)
    process_flow_step_key_id: Optional[int] = Field(default=None, gt=0)
    revision: str = Field(default="REV.0", max_length=20)
    management_no: Optional[str] = Field(default=None, max_length=80)
    effective_date: Optional[str] = Field(default=None, max_length=10)
    change_summary: Optional[str] = None
    change_reason: Optional[str] = None
    note: Optional[str] = None
    prepared_by: Optional[str] = Field(default=None, max_length=100)
    reviewed_by: Optional[str] = Field(default=None, max_length=100)
    approved_by: Optional[str] = Field(default=None, max_length=100)
    prechecks: list[PrecheckPayload] = Field(default_factory=list)
    items: list[InspectionItemPayload] = Field(default_factory=list)


class RevisionPayload(BaseModel):
    revision: str = Field(max_length=20)
    change_summary: Optional[str] = None
    change_reason: Optional[str] = None


def _clean(value):
    return str(value or "").strip() or None


def _user_name(user):
    return str(getattr(user, "name", None) or getattr(user, "username", None) or "").strip()


def _menu_level(user, document_type: str):
    if check_admin_permission(user):
        return "WRITE"
    permissions = parse_user_permissions(user)
    access = permissions.get("menu_access")
    path = TYPE_PATHS[document_type]
    if isinstance(access, dict) and access:
        value = access.get(path)
        if value is not None:
            if value is True:
                return "READ"
            level = str(value or "NONE").upper()
            return level if level in {"NONE", "READ", "WRITE"} else "NONE"
        # 기존 입고검사 기준서 권한은 입고 탭에만 호환합니다.
        if document_type == "INBOUND":
            old_value = access.get(LEGACY_INBOUND_PATH)
            if old_value is True:
                return "READ"
            old_level = str(old_value or "NONE").upper()
            if old_level in {"READ", "WRITE"}:
                return old_level
    return "NONE"


def _require_access(user, document_type: str, write: bool = False):
    document_type = str(document_type or "").strip().upper()
    if document_type not in VALID_TYPES:
        raise HTTPException(422, "지원하지 않는 검사기준서 구분입니다.")
    level = _menu_level(user, document_type)
    if write and level != "WRITE":
        raise HTTPException(403, "해당 검사기준서의 쓰기 권한이 필요합니다.")
    if not write and level == "NONE":
        raise HTTPException(403, "해당 검사기준서의 조회 권한이 없습니다.")
    return document_type


def _numeric_revision(value: str):
    try:
        revision = normalize_revision_code(value)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if len(revision) > 20:
        raise HTTPException(422, "개정번호는 숫자 16자리 이내로 입력해 주세요.")
    return revision


def _identity_filter(query, document_type, item_id, step_key_id):
    query = query.filter(
        InspectionStandard.document_type == document_type,
        InspectionStandard.item_id == item_id,
    )
    if document_type == "PROCESS":
        return query.filter(InspectionStandard.process_flow_step_key_id == step_key_id)
    return query.filter(InspectionStandard.process_flow_step_key_id.is_(None))


def _resolve_item(db: Session, document_type: str, item_id: int):
    item = db.get(ItemMasterModel, item_id)
    if not item or item.is_active != "Y":
        raise HTTPException(422, "사용 가능한 품번을 선택해 주세요.")
    if document_type == "FINAL" and not is_selectable_finished_item(db, item):
        raise HTTPException(422, "최종검사 기준서는 사용 중인 완제품 품번만 등록할 수 있습니다.")
    return item


def _resolve_process_step(db: Session, item_id: int, step_key_id: Optional[int]):
    if not step_key_id:
        raise HTTPException(422, "공정검사 기준서는 공정흐름도 단계를 선택해야 합니다.")
    key = db.get(ProcessFlowStepKey, step_key_id)
    if not key or key.item_id != item_id:
        raise HTTPException(422, "선택한 공정흐름도 단계가 해당 품번과 일치하지 않습니다.")
    current = (
        db.query(ProcessFlowRevision)
        .filter(ProcessFlowRevision.item_id == item_id, ProcessFlowRevision.status == "CURRENT")
        .first()
    )
    step = None
    if current:
        step = (
            db.query(ProcessFlowStep)
            .filter(ProcessFlowStep.revision_id == current.id, ProcessFlowStep.step_key_id == step_key_id)
            .first()
        )
    if not step:
        step = (
            db.query(ProcessFlowStep)
            .filter(ProcessFlowStep.step_key_id == step_key_id)
            .order_by(ProcessFlowStep.id.desc())
            .first()
        )
    if not step:
        raise HTTPException(422, "공정흐름도 단계 정보를 찾을 수 없습니다.")
    return step


def _validate_items(items):
    if not items:
        raise HTTPException(422, "검사항목을 1개 이상 등록해 주세요.")
    for row in items:
        if not row.inspection_item_name.strip():
            raise HTTPException(422, "검사항목명은 필수입니다.")
        if row.lower_limit is not None and row.upper_limit is not None and row.lower_limit > row.upper_limit:
            raise HTTPException(422, "규격 하한은 상한보다 클 수 없습니다.")


def _replace_children(row: InspectionStandard, payload: StandardPayload):
    row.prechecks.clear()
    for p in sorted(payload.prechecks, key=lambda x: x.sort_order):
        if not p.check_item.strip():
            continue
        row.prechecks.append(InspectionStandardPrecheck(
            sort_order=p.sort_order,
            check_item=p.check_item.strip(),
            criteria=_clean(p.criteria),
            responsible=_clean(p.responsible),
            frequency=_clean(p.frequency),
            abnormal_action=_clean(p.abnormal_action),
            note=_clean(p.note),
        ))
    row.items.clear()
    for x in sorted(payload.items, key=lambda x: x.sort_order):
        row.items.append(InspectionStandardItem(
            sort_order=x.sort_order,
            inspection_group_no=_clean(x.inspection_group_no),
            inspection_item_name=x.inspection_item_name.strip(),
            detail_no=_clean(x.detail_no),
            special_characteristic=_clean(x.special_characteristic),
            inspection_tool=_clean(x.inspection_tool),
            spec_text=_clean(x.spec_text),
            nominal_value=x.nominal_value,
            lower_limit=x.lower_limit,
            upper_limit=x.upper_limit,
            unit=_clean(x.unit),
            inspection_frequency=_clean(x.inspection_frequency),
            sample_qty_text=_clean(x.sample_qty_text),
            record_management=_clean(x.record_management),
            note=_clean(x.note),
        ))


def _standard_dict(row: InspectionStandard, include_children=False):
    data = {
        "id": row.id,
        "document_type": row.document_type,
        "document_name": DOC_NAMES.get(row.document_type, row.document_type),
        "item_id": row.item_id,
        "process_flow_step_key_id": row.process_flow_step_key_id,
        "revision": row.revision,
        "sequence": row.sequence,
        "status": row.status,
        "management_no": row.management_no or "",
        "effective_date": row.effective_date or "",
        "change_summary": row.change_summary or "",
        "change_reason": row.change_reason or "",
        "note": row.note or "",
        "part_no": row.part_no_snapshot,
        "part_name": row.part_name_snapshot,
        "process_step_no": row.process_step_no_snapshot or "",
        "process_step_name": row.process_step_name_snapshot or "",
        "prepared_by": row.prepared_by or "",
        "reviewed_by": row.reviewed_by or "",
        "approved_by": row.approved_by or "",
        "previous_revision_id": row.previous_revision_id,
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
        "activated_at": row.activated_at.isoformat(sep=" ", timespec="seconds") if row.activated_at else "",
    }
    if include_children:
        data["prechecks"] = [{
            "id": x.id, "sort_order": x.sort_order, "check_item": x.check_item,
            "criteria": x.criteria or "", "responsible": x.responsible or "",
            "frequency": x.frequency or "", "abnormal_action": x.abnormal_action or "", "note": x.note or "",
        } for x in row.prechecks]
        data["items"] = [{
            "id": x.id, "sort_order": x.sort_order, "inspection_group_no": x.inspection_group_no or "",
            "inspection_item_name": x.inspection_item_name, "detail_no": x.detail_no or "",
            "special_characteristic": x.special_characteristic or "", "inspection_tool": x.inspection_tool or "",
            "spec_text": x.spec_text or "", "nominal_value": x.nominal_value, "lower_limit": x.lower_limit,
            "upper_limit": x.upper_limit, "unit": x.unit or "",
            "inspection_frequency": x.inspection_frequency or "", "sample_qty_text": x.sample_qty_text or "",
            "record_management": x.record_management or "", "note": x.note or "",
        } for x in row.items]
    return data


@router.get(ROOT_PATH, response_class=HTMLResponse)
def inspection_entry(request: Request, current_user=Depends(get_current_user)):
    for doc_type in ("INBOUND", "PROCESS", "FINAL"):
        if _menu_level(current_user, doc_type) in {"READ", "WRITE"}:
            return RedirectResponse(TYPE_PATHS[doc_type], status_code=303)
    raise HTTPException(403, "검사기준서 관리 조회 권한이 없습니다.")


@router.get(ROOT_PATH + "/{kind}", response_class=HTMLResponse)
def inspection_page(kind: str, request: Request, current_user=Depends(get_current_user)):
    mapping = {"inbound": "INBOUND", "process": "PROCESS", "final": "FINAL"}
    document_type = mapping.get(kind.lower())
    if not document_type:
        raise HTTPException(404, "검사기준서 화면을 찾을 수 없습니다.")
    _require_access(current_user, document_type)
    return templates.TemplateResponse(
        request=request,
        name="standard_documents/inspection_standards.html",
        context={
            "request": request,
            "user": current_user,
            "document_type": document_type,
            "document_name": DOC_NAMES[document_type],
            "tab_paths": TYPE_PATHS,
            "tab_access": {x: _menu_level(current_user, x) for x in ("INBOUND", "PROCESS", "FINAL")},
            "can_write": _menu_level(current_user, document_type) == "WRITE",
        },
    )


@router.get("/api/standard-documents/inspection-standards/users")
def standard_users(current_user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not any(_menu_level(current_user, x) != "NONE" for x in VALID_TYPES):
        raise HTTPException(403, "검사기준서 관리 조회 권한이 없습니다.")
    rows = db.query(UserModel).filter(UserModel.is_active == True).order_by(UserModel.name, UserModel.username).all()
    return [{"id": x.id, "name": str(x.name or x.username), "username": x.username} for x in rows]


@router.get("/api/standard-documents/inspection-standards/items")
def selectable_items(
    document_type: str,
    keyword: Optional[str] = Query(None, max_length=100),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document_type = _require_access(current_user, document_type)
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    if document_type == "FINAL":
        from services.standard_document_item_service import finished_item_condition
        query = query.filter(finished_item_condition())
    if keyword and keyword.strip():
        value = f"%{keyword.strip()}%"
        query = query.filter(
            (ItemMasterModel.part_no.ilike(value)) | (ItemMasterModel.part_name.ilike(value))
        )
    rows = query.order_by(ItemMasterModel.part_no).limit(300).all()
    return [{"item_id": x.id, "part_no": x.part_no, "part_name": x.part_name or ""} for x in rows]


@router.get("/api/standard-documents/inspection-standards/process-steps")
def process_steps(
    item_id: int = Query(..., gt=0),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_access(current_user, "PROCESS")
    current = (
        db.query(ProcessFlowRevision)
        .filter(ProcessFlowRevision.item_id == item_id, ProcessFlowRevision.status == "CURRENT")
        .first()
    )
    if not current:
        return []
    rows = (
        db.query(ProcessFlowStep)
        .filter(ProcessFlowStep.revision_id == current.id, ProcessFlowStep.retired_at.is_(None))
        .order_by(ProcessFlowStep.sort_order, ProcessFlowStep.id)
        .all()
    )
    return [{
        "step_key_id": x.step_key_id,
        "step_no": x.step_no,
        "step_name": x.step_name,
        "sort_order": x.sort_order,
        "symbol_name": x.symbol_name_snapshot or "",
    } for x in rows]


@router.get("/api/standard-documents/inspection-standards")
def list_standards(
    document_type: str,
    item_id: Optional[int] = Query(None, gt=0),
    process_flow_step_key_id: Optional[int] = Query(None, gt=0),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document_type = _require_access(current_user, document_type)
    query = db.query(InspectionStandard).filter(InspectionStandard.document_type == document_type)
    if item_id:
        query = query.filter(InspectionStandard.item_id == item_id)
    if document_type == "PROCESS" and process_flow_step_key_id:
        query = query.filter(InspectionStandard.process_flow_step_key_id == process_flow_step_key_id)
    rows = query.order_by(InspectionStandard.item_id, InspectionStandard.sequence.desc(), InspectionStandard.id.desc()).all()
    return [_standard_dict(x) for x in rows]


@router.get("/api/standard-documents/inspection-standards/{standard_id}")
def get_standard(
    standard_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(InspectionStandard, standard_id)
    if not row:
        raise HTTPException(404, "검사기준서를 찾을 수 없습니다.")
    _require_access(current_user, row.document_type)
    return _standard_dict(row, include_children=True)


@router.post("/api/standard-documents/inspection-standards")
def create_standard(
    payload: StandardPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document_type = _require_access(current_user, payload.document_type, write=True)
    item = _resolve_item(db, document_type, payload.item_id)
    step = _resolve_process_step(db, item.id, payload.process_flow_step_key_id) if document_type == "PROCESS" else None
    revision = _numeric_revision(payload.revision)
    _validate_items(payload.items)
    query = _identity_filter(db.query(InspectionStandard), document_type, item.id, step.step_key_id if step else None)
    if any(revision_key(x.revision) == revision_key(revision) for x in query.all()):
        raise HTTPException(409, "같은 품번/검사구분의 동일 REV 기준서가 이미 존재합니다.")
    last = query.order_by(InspectionStandard.sequence.desc()).first()
    row = InspectionStandard(
        document_type=document_type,
        item_id=item.id,
        process_flow_step_key_id=step.step_key_id if step else None,
        revision=revision,
        sequence=(last.sequence + 1) if last else 1,
        status="DRAFT",
        previous_revision_id=last.id if last else None,
        management_no=_clean(payload.management_no),
        effective_date=_clean(payload.effective_date),
        change_summary=_clean(payload.change_summary),
        change_reason=_clean(payload.change_reason),
        note=_clean(payload.note),
        part_no_snapshot=item.part_no,
        part_name_snapshot=item.part_name or "",
        process_step_no_snapshot=step.step_no if step else None,
        process_step_name_snapshot=step.step_name if step else None,
        prepared_by=_clean(payload.prepared_by),
        reviewed_by=_clean(payload.reviewed_by),
        approved_by=_clean(payload.approved_by),
        created_by=_user_name(current_user),
    )
    _replace_children(row, payload)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _standard_dict(row, include_children=True)


@router.put("/api/standard-documents/inspection-standards/{standard_id}")
def update_standard(
    standard_id: int,
    payload: StandardPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(InspectionStandard, standard_id)
    if not row:
        raise HTTPException(404, "검사기준서를 찾을 수 없습니다.")
    _require_access(current_user, row.document_type, write=True)
    if row.status != "DRAFT":
        raise HTTPException(409, "작성중 기준서만 수정할 수 있습니다. 현재사용 문서는 개정해 주세요.")
    if payload.document_type.strip().upper() != row.document_type or payload.item_id != row.item_id:
        raise HTTPException(422, "기준서 구분과 품번은 작성 후 변경할 수 없습니다.")
    if row.document_type == "PROCESS" and payload.process_flow_step_key_id != row.process_flow_step_key_id:
        raise HTTPException(422, "공정검사 기준서의 공정흐름도 단계는 작성 후 변경할 수 없습니다.")
    revision = _numeric_revision(payload.revision)
    query = _identity_filter(
        db.query(InspectionStandard), row.document_type, row.item_id, row.process_flow_step_key_id
    ).filter(InspectionStandard.id != row.id)
    if any(revision_key(x.revision) == revision_key(revision) for x in query.all()):
        raise HTTPException(409, "동일 REV 기준서가 이미 존재합니다.")
    _validate_items(payload.items)
    row.revision = revision
    row.management_no = _clean(payload.management_no)
    row.effective_date = _clean(payload.effective_date)
    row.change_summary = _clean(payload.change_summary)
    row.change_reason = _clean(payload.change_reason)
    row.note = _clean(payload.note)
    row.prepared_by = _clean(payload.prepared_by)
    row.reviewed_by = _clean(payload.reviewed_by)
    row.approved_by = _clean(payload.approved_by)
    _replace_children(row, payload)
    db.commit()
    db.refresh(row)
    return _standard_dict(row, include_children=True)


@router.post("/api/standard-documents/inspection-standards/{standard_id}/revise")
def revise_standard(
    standard_id: int,
    payload: RevisionPayload,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = db.get(InspectionStandard, standard_id)
    if not source:
        raise HTTPException(404, "검사기준서를 찾을 수 없습니다.")
    _require_access(current_user, source.document_type, write=True)
    if source.status == "RETIRED":
        raise HTTPException(409, "폐기된 기준서는 개정할 수 없습니다.")
    revision = _numeric_revision(payload.revision)
    query = _identity_filter(
        db.query(InspectionStandard), source.document_type, source.item_id, source.process_flow_step_key_id
    )
    if any(revision_key(x.revision) == revision_key(revision) for x in query.all()):
        raise HTTPException(409, "동일 REV 기준서가 이미 존재합니다.")
    last = query.order_by(InspectionStandard.sequence.desc()).first()
    row = InspectionStandard(
        document_type=source.document_type,
        item_id=source.item_id,
        process_flow_step_key_id=source.process_flow_step_key_id,
        revision=revision,
        sequence=(last.sequence + 1) if last else source.sequence + 1,
        status="DRAFT",
        previous_revision_id=source.id,
        management_no=source.management_no,
        effective_date=source.effective_date,
        change_summary=_clean(payload.change_summary),
        change_reason=_clean(payload.change_reason),
        note=source.note,
        part_no_snapshot=source.part_no_snapshot,
        part_name_snapshot=source.part_name_snapshot,
        process_step_no_snapshot=source.process_step_no_snapshot,
        process_step_name_snapshot=source.process_step_name_snapshot,
        prepared_by=source.prepared_by,
        reviewed_by=source.reviewed_by,
        approved_by=source.approved_by,
        control_plan_revision_id=source.control_plan_revision_id,
        control_plan_process_no=source.control_plan_process_no,
        control_plan_item_key=source.control_plan_item_key,
        created_by=_user_name(current_user),
    )
    for p in source.prechecks:
        row.prechecks.append(InspectionStandardPrecheck(
            sort_order=p.sort_order, check_item=p.check_item, criteria=p.criteria,
            responsible=p.responsible, frequency=p.frequency, abnormal_action=p.abnormal_action, note=p.note,
        ))
    for x in source.items:
        row.items.append(InspectionStandardItem(
            sort_order=x.sort_order, inspection_group_no=x.inspection_group_no,
            inspection_item_name=x.inspection_item_name, detail_no=x.detail_no,
            special_characteristic=x.special_characteristic, inspection_tool=x.inspection_tool,
            spec_text=x.spec_text, nominal_value=x.nominal_value, lower_limit=x.lower_limit,
            upper_limit=x.upper_limit, unit=x.unit, inspection_frequency=x.inspection_frequency,
            sample_qty_text=x.sample_qty_text, record_management=x.record_management, note=x.note,
        ))
    db.add(row)
    db.commit()
    db.refresh(row)
    return _standard_dict(row, include_children=True)


@router.post("/api/standard-documents/inspection-standards/{standard_id}/activate")
def activate_standard(
    standard_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(InspectionStandard, standard_id)
    if not row:
        raise HTTPException(404, "검사기준서를 찾을 수 없습니다.")
    _require_access(current_user, row.document_type, write=True)
    if row.status != "DRAFT":
        raise HTTPException(409, "작성중 기준서만 현재사용으로 적용할 수 있습니다.")
    now = datetime.now()
    query = _identity_filter(
        db.query(InspectionStandard), row.document_type, row.item_id, row.process_flow_step_key_id
    ).filter(InspectionStandard.status == "CURRENT", InspectionStandard.id != row.id)
    for old in query.all():
        old.status = "SUPERSEDED"
        old.superseded_at = now
    row.status = "CURRENT"
    row.activated_at = now
    db.commit()
    db.refresh(row)
    return _standard_dict(row, include_children=True)


@router.post("/api/standard-documents/inspection-standards/{standard_id}/retire")
def retire_standard(
    standard_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(InspectionStandard, standard_id)
    if not row:
        raise HTTPException(404, "검사기준서를 찾을 수 없습니다.")
    _require_access(current_user, row.document_type, write=True)
    if row.status == "SUPERSEDED":
        raise HTTPException(409, "이전 REV는 개정이력으로 보존됩니다.")
    row.status = "RETIRED"
    row.retired_at = datetime.now()
    db.commit()
    return _standard_dict(row)


@router.delete("/api/standard-documents/inspection-standards/{standard_id}")
def delete_draft(
    standard_id: int,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(InspectionStandard, standard_id)
    if not row:
        raise HTTPException(404, "검사기준서를 찾을 수 없습니다.")
    _require_access(current_user, row.document_type, write=True)
    if row.status != "DRAFT":
        raise HTTPException(409, "작성중 기준서만 삭제할 수 있습니다.")
    db.delete(row)
    db.commit()
    return {"message": "작성중 검사기준서를 삭제했습니다."}
