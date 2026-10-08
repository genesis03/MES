from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from core import config
from core.database import get_db
from services.document_correction_service import validate_correction, record_correction, correction_history
from services.revision_number_service import normalize_revision_code, revision_key
from core.security import get_current_user, get_current_user_optional
from models.document import DocumentFile, ItemDocument, ItemRevision
from models.document_migration import DRAWING_TYPE
from models.item_identity import ItemPartNoHistory
from models.models import CommonCodeModel, ItemMasterModel
from services.document_service import (
    DOCUMENT_MENU_PATH, actor_name, check_file_integrity, cleanup_failed_uploads, document_code_options,
    document_dict, get_revision, has_document_access, lock_item, lock_revision, require_document_access,
    revision_dict, store_document_files, validate_common_code,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


class RevisionPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    revision_code: str = Field(min_length=1, max_length=50)
    _normalize_revision = field_validator("revision_code", mode="before")(normalize_revision_code)
    previous_revision_id: int | None = Field(default=None, gt=0)
    change_reason: str = Field(default="", max_length=4000)
    eco_no: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=8000)


class ReasonPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=4000)


class RevisionCorrectionPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    eco_no: str = Field(default="", max_length=100)
    change_reason: str = Field(default="", max_length=4000)
    note: str = Field(default="", max_length=8000)
    correction_reason: str = Field(min_length=1, max_length=4000)
    correction_token: str = Field(min_length=64, max_length=64)


class DocumentCorrectionPayload(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    document_no: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=8000)
    correction_reason: str = Field(min_length=1, max_length=4000)
    correction_token: str = Field(min_length=64, max_length=64)


@router.put("/api/documents/revisions/{revision_id}")
def correct_revision(revision_id: int, payload: RevisionCorrectionPayload,
                     db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    _, row = lock_revision(db, revision_id)
    if row.status not in {"DRAFT", "CURRENT"}:
        raise HTTPException(409, "초안 또는 현재 사용 도면만 수정할 수 있습니다.")
    before = revision_dict(row)
    validate_correction(payload.correction_reason, payload.correction_token, before)
    row.eco_no, row.change_reason, row.note = payload.eco_no or None, payload.change_reason or None, payload.note or None
    record_correction(db, user, "item_revisions", row.id, payload.correction_reason, before, revision_dict(row))
    _commit(db)
    return revision_dict(row)


@router.put("/api/documents/{document_id}/metadata")
def correct_document(document_id: int, payload: DocumentCorrectionPayload,
                     db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    row = db.get(ItemDocument, document_id)
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    _, revision = lock_revision(db, row.revision_id)
    db.refresh(row)
    if revision.status not in {"DRAFT", "CURRENT"} or row.retired_at:
        raise HTTPException(409, "초안 또는 현재 사용 문서의 기본정보만 수정할 수 있습니다.")
    before = document_dict(db, row)
    validate_correction(payload.correction_reason, payload.correction_token, before)
    row.title, row.document_no, row.note = payload.title, payload.document_no or None, payload.note or None
    record_correction(db, user, "item_documents", row.id, payload.correction_reason, before, document_dict(db, row))
    _commit(db)
    return document_dict(db, row)


@router.get("/api/document-corrections/{kind}/{record_id}")
def read_corrections(kind: str, record_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if kind in {"drawing", "drawing-file"}:
        require_document_access(user)
        row = db.get(ItemRevision if kind == "drawing" else ItemDocument, record_id)
        table = "item_revisions" if kind == "drawing" else "item_documents"
    elif kind == "fmea":
        from services.fmea_service import require_fmea_access
        from models.fmea import FmeaRevision
        require_fmea_access(user)
        row, table = db.get(FmeaRevision, record_id), "fmea_revisions"
    elif kind == "control-plan":
        from routers.control_plan import access
        from models.control_plan import ControlPlanRevision
        access(user)
        row, table = db.get(ControlPlanRevision, record_id), "control_plan_revisions"
    elif kind == "inspection":
        from routers.inspection_standards import _require_access
        from models.inspection_standard import InspectionStandard
        row, table = db.get(InspectionStandard, record_id), "inspection_standards"
        if row:
            _require_access(user, row.document_type)
    else:
        raise HTTPException(404, "문서 유형을 찾을 수 없습니다.")
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    return correction_history(db, table, record_id)


def _commit(db):
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(409, "중복 Revision 또는 동시 변경이 발생했습니다. 새로 조회 후 다시 시도해 주세요.") from exc


@router.get(DOCUMENT_MENU_PATH, response_class=HTMLResponse)
def drawing_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_document_access(user)
    return templates.TemplateResponse(request=request, name="drawing_management.html",
        context={"user": user, "can_write_documents": has_document_access(user, "WRITE")})


@router.get("/api/documents/options")
def document_options(db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user)
    return {"document_types": document_code_options(db, "DOCUMENT_TYPE"),
            "file_roles": document_code_options(db, "DOCUMENT_FILE_ROLE"), "drawing_type": DRAWING_TYPE,
            "max_file_bytes": config.DOCUMENT_MAX_FILE_BYTES, "max_files": config.DOCUMENT_MAX_FILES,
            "allowed_extensions": config.DOCUMENT_ALLOWED_EXTENSIONS}


@router.get("/api/documents/items")
def drawing_items(keyword: str = "", drawing_no: str = "", drawing_state: str = "",
                  item_id: int | None = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user)
    current = select(ItemRevision).where(ItemRevision.status == "CURRENT").subquery()
    document_count = select(ItemDocument.revision_id, func.count(ItemDocument.id).label("count")).where(
        ItemDocument.document_type == DRAWING_TYPE, ItemDocument.retired_at.is_(None)
    ).group_by(ItemDocument.revision_id).subquery()
    query = select(ItemMasterModel, current.c.id, current.c.revision_code,
                   func.coalesce(document_count.c.count, 0)).outerjoin(current, current.c.item_id == ItemMasterModel.id
                   ).outerjoin(document_count, document_count.c.revision_id == current.c.id)
    if item_id is not None:
        query = query.where(ItemMasterModel.id == item_id)
    if keyword.strip():
        value = f"%{keyword.strip()}%"
        history = select(ItemPartNoHistory.item_id).where(
            ItemPartNoHistory.old_part_no.ilike(value) | ItemPartNoHistory.new_part_no.ilike(value))
        query = query.where(ItemMasterModel.part_no.ilike(value) | ItemMasterModel.part_name.ilike(value)
                            | ItemMasterModel.id.in_(history))
    if drawing_no.strip():
        matching = select(ItemRevision.item_id).join(ItemDocument, ItemDocument.revision_id == ItemRevision.id).where(
            ItemDocument.document_type == DRAWING_TYPE, ItemDocument.document_no.ilike(f"%{drawing_no.strip()}%"))
        query = query.where(ItemMasterModel.id.in_(matching))
    if drawing_state == "CURRENT":
        query = query.where(document_count.c.count > 0)
    elif drawing_state == "MISSING":
        query = query.where(func.coalesce(document_count.c.count, 0) == 0)
    elif drawing_state:
        raise HTTPException(422, "조회 상태가 올바르지 않습니다.")
    rows = db.execute(query.order_by(ItemMasterModel.part_no)).all()
    return [{"item_id": item.id, "part_no": item.part_no, "part_name": item.part_name,
             "is_active": item.is_active,
             "current_revision_id": revision_id, "current_revision": code, "drawing_count": count}
            for item, revision_id, code, count in rows]




@router.get("/api/documents/items/{item_id}/current-view", response_class=HTMLResponse)
def current_drawing_view(item_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    require_document_access(user)
    item = db.get(ItemMasterModel, item_id)
    if not item:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")
    revision = db.scalar(select(ItemRevision).where(
        ItemRevision.item_id == item_id, ItemRevision.status == "CURRENT"))
    documents = []
    if revision:
        rows = db.scalars(select(ItemDocument).where(
            ItemDocument.revision_id == revision.id,
            ItemDocument.document_type == DRAWING_TYPE,
            ItemDocument.retired_at.is_(None)
        ).order_by(ItemDocument.id)).all()
        documents = [document_dict(db, row) for row in rows]
    previews = [file for document in documents for file in document["files"] if file["can_preview"]]
    if len(previews) == 1:
        # 기존 파일 응답의 형식/경로/크기/체크섬 검사를 그대로 사용합니다.
        return _file_response(db, previews[0]["id"], True)
    return templates.TemplateResponse(request=request, name="current_drawing_view.html",
        context={"item": item, "revision": revision, "documents": documents},
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; script-src 'none'",
                 "Referrer-Policy": "no-referrer"})


@router.get("/api/documents/items/{item_id}/revisions")
def item_revisions(item_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user)
    if not db.get(ItemMasterModel, item_id):
        raise HTTPException(404, "품목을 찾을 수 없습니다.")
    rows = db.scalars(select(ItemRevision).where(ItemRevision.item_id == item_id).order_by(ItemRevision.sequence.desc())).all()
    return [revision_dict(row) for row in rows]


@router.post("/api/documents/items/{item_id}/revisions", status_code=201)
def create_revision(item_id: int, payload: RevisionPayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    item = lock_item(db, item_id)
    previous = None
    if payload.previous_revision_id:
        previous = get_revision(db, payload.previous_revision_id)
        if previous.item_id != item.id or previous.status == "RETIRED":
            raise HTTPException(422, "같은 품목의 유효한 이전 Revision을 선택해 주세요.")
    elif db.scalar(select(ItemRevision.id).where(ItemRevision.item_id == item_id)):
        raise HTTPException(422, "개정 등록은 이전 Revision을 선택해 주세요.")
    if previous and not payload.change_reason:
        raise HTTPException(422, "개정 사유를 입력해 주세요.")
    codes = db.scalars(select(ItemRevision.revision_code).where(ItemRevision.item_id == item_id))
    if any(revision_key(code) == revision_key(payload.revision_code) for code in codes):
        raise HTTPException(409, "이 품목에 동일 Revision이 이미 등록되어 있습니다.")
    sequence = (db.scalar(select(func.max(ItemRevision.sequence)).where(ItemRevision.item_id == item_id)) or 0) + 1
    row = ItemRevision(item_id=item.id, revision_code=payload.revision_code, sequence=sequence,
                       previous_revision_id=previous.id if previous else None, change_reason=payload.change_reason or None,
                       note=payload.note or None, eco_no=payload.eco_no.strip() or None, created_by_id=user.id, created_by=actor_name(user))
    db.add(row)
    _commit(db)
    db.refresh(row)
    return revision_dict(row)


@router.get("/api/documents/revisions/{revision_id}/documents")
def revision_documents(revision_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user)
    get_revision(db, revision_id)
    types = {row.code: row.code_name for row in db.scalars(select(CommonCodeModel).where(CommonCodeModel.group_code == "DOCUMENT_TYPE"))}
    rows = db.scalars(select(ItemDocument).where(ItemDocument.revision_id == revision_id).order_by(ItemDocument.id)).all()
    return [document_dict(db, row, types) for row in rows]


@router.post("/api/documents/revisions/{revision_id}/documents", status_code=201)
def upload_document(revision_id: int, title: str = Form(...), document_no: str = Form(""),
                    document_revision: str = Form(""), note: str = Form(""),
                    document_type: str = Form(...), files: list[UploadFile] = File(...),
                    db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    created_paths = []
    try:
        _, revision = lock_revision(db, revision_id)
        if revision.status != "DRAFT":
            raise HTTPException(409, "초안 Revision에만 파일을 등록할 수 있습니다. 개정 등록을 이용해 주세요.")
        validate_common_code(db, "DOCUMENT_TYPE", document_type)
        if not title.strip() or len(title.strip()) > 200 or len(document_no.strip()) > 100 or len(document_revision.strip()) > 50 or len(note) > 8000:
            raise HTTPException(422, "문서 제목 또는 입력 길이가 올바르지 않습니다.")
        row = ItemDocument(revision_id=revision.id, document_type=document_type, document_no=document_no.strip() or None,
                           title=title.strip(), document_revision=(revision.revision_code if document_type == DRAWING_TYPE
                               else document_revision.strip() or None), note=note.strip() or None,
                           created_by_id=user.id, created_by=actor_name(user))
        db.add(row)
        db.flush()
        store_document_files(db, revision, row, files, user, created_paths)
        _commit(db)
        db.refresh(row)
        return document_dict(db, row)
    except Exception:
        db.rollback()
        cleanup_failed_uploads(created_paths)
        raise
    finally:
        for upload in files:
            upload.file.close()


@router.post("/api/documents/revisions/{revision_id}/activate")
def activate_revision(revision_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    item, revision = lock_revision(db, revision_id)
    if revision.status != "DRAFT":
        raise HTTPException(409, "초안 Revision만 현재 사용으로 적용할 수 있습니다.")
    documents = db.scalars(select(ItemDocument).where(ItemDocument.revision_id == revision.id, ItemDocument.retired_at.is_(None))).all()
    if not any(row.document_type == DRAWING_TYPE for row in documents):
        raise HTTPException(422, "도면을 등록한 후 현재 사용으로 적용해 주세요.")
    for document in documents:
        file_rows = db.scalars(select(DocumentFile).where(DocumentFile.document_id == document.id)).all()
        if not file_rows:
            raise HTTPException(422, "파일이 없는 문서는 적용할 수 없습니다.")
        for file in file_rows:
            check_file_integrity(file)
    now = datetime.now()
    old_rows = db.scalars(select(ItemRevision).where(ItemRevision.item_id == item.id, ItemRevision.status == "CURRENT")).all()
    for old in old_rows:
        if revision.sequence <= old.sequence:
            raise HTTPException(409, "현재 사용보다 이전 순서의 Revision은 적용할 수 없습니다. 새 개정을 등록해 주세요.")
        old.status, old.superseded_at = "SUPERSEDED", now
    # 고유 인덱스가 새 CURRENT를 검사하기 전에 이전 CURRENT를 먼저 해제합니다.
    db.flush()
    revision.status, revision.activated_at, revision.activated_by_id = "CURRENT", now, user.id
    _commit(db)
    return revision_dict(revision)


@router.post("/api/documents/revisions/{revision_id}/retire")
def retire_revision(revision_id: int, payload: ReasonPayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    _, revision = lock_revision(db, revision_id, require_active=False)
    if revision.status == "RETIRED":
        raise HTTPException(409, "이미 폐기된 Revision입니다.")
    revision.status, revision.retired_at, revision.retired_by_id = "RETIRED", datetime.now(), user.id
    revision.retire_reason = payload.reason
    # 현재 도면 폐기 후 구버전을 자동 재적용하지 않습니다. 기존 품목 revision 값도 수정하지 않습니다.
    _commit(db)
    return revision_dict(revision)


@router.post("/api/documents/{document_id}/retire")
def retire_document(document_id: int, payload: ReasonPayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user, "WRITE")
    row = db.get(ItemDocument, document_id)
    if not row:
        raise HTTPException(404, "문서를 찾을 수 없습니다.")
    _, revision = lock_revision(db, row.revision_id, require_active=False)
    db.refresh(row)
    if revision.status != "DRAFT":
        raise HTTPException(409, "적용된 문서의 구성은 변경할 수 없습니다. 새 개정 또는 Revision 폐기를 이용해 주세요.")
    if row.retired_at:
        raise HTTPException(409, "이미 폐기된 문서입니다.")
    row.retired_at, row.retired_by_id, row.retire_reason = datetime.now(), user.id, payload.reason
    _commit(db)
    return document_dict(db, row)


def _file_response(db, file_id, preview):
    row = db.get(DocumentFile, file_id)
    if not row:
        raise HTTPException(404, "문서 파일을 찾을 수 없습니다.")
    if preview and row.extension not in {"pdf", "png", "jpg", "jpeg", "bmp"}:
        raise HTTPException(415, "이 형식은 다운로드하여 확인해 주세요.")
    path = check_file_integrity(row)
    # PDF를 최상위 새 탭에서 표시합니다. CSP sandbox는 Chrome PDF 뷰어를 차단합니다.
    # 미리보기는 기존 형식/권한/무결성 검사로 제한하고 다른 페이지의 삽입은 차단합니다.
    policy = "frame-ancestors 'none'; base-uri 'none'; form-action 'none'" if preview else "sandbox"
    return FileResponse(path, media_type=row.media_type, filename=row.original_name,
                        content_disposition_type="inline" if preview else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                                 "Content-Security-Policy": policy})


@router.get("/api/documents/files/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user)
    return _file_response(db, file_id, False)


@router.get("/api/documents/files/{file_id}/preview")
def preview_file(file_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    require_document_access(user)
    return _file_response(db, file_id, True)
