"""문서 권한, 품목 잠금, 파일 검증/보관. DB 식별자로만 파일을 찾습니다."""
import hashlib
import warnings
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
from pypdf import PdfReader
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from core import config
from core.security import check_admin_permission, check_permission, parse_user_permissions
from models.document import DocumentFile, ItemDocument, ItemRevision
from models.models import CommonCodeModel, ItemMasterModel


DOCUMENT_MENU_PATH = "/basic-info/drawings"
PREVIEW_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "tif": "image/tiff", "tiff": "image/tiff", "bmp": "image/bmp"}
IMAGE_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tif": "TIFF", "tiff": "TIFF", "bmp": "BMP"}
CAD_EXTENSIONS = {"dwg", "dxf", "step", "stp", "igs", "iges"}


def has_document_access(user, action="READ"):
    if not user:
        return False
    if check_admin_permission(user):
        return True
    permissions = parse_user_permissions(user)
    access = permissions.get("menu_access")
    if isinstance(access, dict) and access:
        level = access.get(DOCUMENT_MENU_PATH)
        if level is True:
            level = "READ"
        level = str(level or "NONE").upper()
        return level == "WRITE" if action == "WRITE" else level in {"READ", "WRITE"}
    return check_permission(user, "basic_info", action)


def require_document_access(user, action="READ"):
    if not has_document_access(user, action):
        raise HTTPException(403, "도면 관리 권한이 없습니다." if action == "READ" else "도면 관리 쓰기 권한이 필요합니다.")


def actor_name(user):
    return str(user.name or user.username)


def lock_item(db, item_id, require_active=True):
    # SQLite는 SELECT FOR UPDATE가 없으므로 먼저 쓰기 잠금을 확보합니다.
    # 모든 등록/적용/폐기 요청이 동일 품목 잠금을 사용합니다.
    if db.get_bind().dialect.name == "sqlite":
        try:
            db.execute(update(ItemMasterModel).where(ItemMasterModel.id == item_id).values(updated_at=ItemMasterModel.updated_at))
        except OperationalError as exc:
            db.rollback()
            raise HTTPException(409, "다른 작업이 처리 중입니다. 잠시 후 다시 시도해 주세요.") from exc
    item = db.scalar(select(ItemMasterModel).where(ItemMasterModel.id == item_id).with_for_update())
    if not item:
        raise HTTPException(404, "품목을 찾을 수 없습니다.")
    if require_active and item.is_active != "Y":
        raise HTTPException(409, "사용중지된 품목은 도면을 등록하거나 적용할 수 없습니다.")
    return item


def get_revision(db, revision_id):
    revision = db.get(ItemRevision, revision_id)
    if not revision:
        raise HTTPException(404, "Revision을 찾을 수 없습니다.")
    return revision


def lock_revision(db, revision_id, require_active=True):
    item_id = db.scalar(select(ItemRevision.item_id).where(ItemRevision.id == revision_id))
    if item_id is None:
        raise HTTPException(404, "Revision을 찾을 수 없습니다.")
    item = lock_item(db, item_id, require_active)
    # 잠금을 기다리는 동안 다른 요청이 상태를 변경했을 수 있습니다.
    revision = db.scalar(select(ItemRevision).where(ItemRevision.id == revision_id).execution_options(populate_existing=True))
    return item, revision


def document_code_options(db, group):
    rows = db.scalars(select(CommonCodeModel).where(
        CommonCodeModel.group_code == group, CommonCodeModel.is_active == "Y"
    ).order_by(CommonCodeModel.sort_order, CommonCodeModel.id)).all()
    return [{"code": row.code, "name": row.code_name} for row in rows]


def validate_common_code(db, group, code):
    if not db.scalar(select(CommonCodeModel.id).where(
        CommonCodeModel.group_code == group, CommonCodeModel.code == code, CommonCodeModel.is_active == "Y"
    )):
        raise HTTPException(422, "사용 가능한 문서 공통코드를 선택해 주세요.")


def resolve_file_path(relative_path):
    root = config.DOCUMENT_STORAGE_ROOT.resolve()
    if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
        raise HTTPException(409, "문서 파일 경로가 올바르지 않습니다.")
    path = (root / relative_path).resolve()
    if path == root or not path.is_relative_to(root):
        raise HTTPException(409, "문서 파일 경로가 올바르지 않습니다.")
    return path


def check_file_integrity(file_row):
    path = resolve_file_path(file_row.relative_path)
    if not path.is_file():
        raise HTTPException(409, "보관 파일을 찾을 수 없습니다. 파일 복구가 필요합니다.")
    if path.stat().st_size != file_row.size_bytes:
        raise HTTPException(409, "보관 파일의 크기가 등록 정보와 다릅니다.")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != file_row.sha256:
        raise HTTPException(409, "보관 파일의 체크섬이 등록 정보와 다릅니다.")
    return path


def _validate_content(path, extension):
    with path.open("rb") as stream:
        header = stream.read(8192)
    try:
        if extension == "pdf":
            if not header.startswith(b"%PDF-"):
                raise ValueError()
            reader = PdfReader(str(path), strict=True)
            if reader.is_encrypted or not len(reader.pages):
                raise ValueError()
        elif extension in IMAGE_FORMATS:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(path) as image:
                    if image.format != IMAGE_FORMATS[extension]:
                        raise ValueError()
                    image.verify()
        elif extension == "dwg":
            if not header.startswith(b"AC10"):
                raise ValueError()
        elif extension == "dxf":
            if not (header.startswith(b"AutoCAD Binary DXF") or b"SECTION" in header.upper()):
                raise ValueError()
        elif extension in {"step", "stp"}:
            if b"ISO-10303-21" not in header.upper():
                raise ValueError()
        elif extension in {"igs", "iges"}:
            if not any(len(line) >= 73 and line[72:73] in (b"S", b"G", b"D") for line in header.splitlines()):
                raise ValueError()
        else:
            raise ValueError()
    except Exception as exc:
        raise HTTPException(422, "확장자와 파일 내용이 일치하지 않거나 읽을 수 없는 파일입니다. 암호화 PDF는 지원하지 않습니다.") from exc
    return PREVIEW_TYPES.get(extension, "application/octet-stream")


def store_document_files(db, revision, document, uploads, user, created_paths):
    if not uploads or len(uploads) > config.DOCUMENT_MAX_FILES:
        raise HTTPException(422, f"파일을 1~{config.DOCUMENT_MAX_FILES}개 선택해 주세요.")
    for upload in uploads:
        name = str(upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not name or len(name) > 255 or any(ord(char) < 32 for char in name):
            raise HTTPException(422, "파일명이 올바르지 않습니다.")
        extension = Path(name).suffix.lower().lstrip(".")
        if extension not in config.DOCUMENT_ALLOWED_EXTENSIONS or extension not in set(PREVIEW_TYPES) | CAD_EXTENSIONS:
            raise HTTPException(422, "허용되지 않은 파일 형식입니다.")
        role = "VIEW" if extension in PREVIEW_TYPES else "SOURCE"
        validate_common_code(db, "DOCUMENT_FILE_ROLE", role)
        relative = f"{revision.item_id}/{revision.id}/{document.id}/{uuid4().hex}.{extension}"
        path = resolve_file_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        with path.open("xb") as target:
            created_paths.append(path)
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > config.DOCUMENT_MAX_FILE_BYTES:
                    raise HTTPException(413, f"파일당 최대 {config.DOCUMENT_MAX_FILE_BYTES // (1024 * 1024)}MB까지 등록할 수 있습니다.")
                digest.update(chunk)
                target.write(chunk)
        if not size:
            raise HTTPException(422, "빈 파일은 등록할 수 없습니다.")
        media_type = _validate_content(path, extension)
        db.add(DocumentFile(document_id=document.id, original_name=name, relative_path=relative,
                            extension=extension, media_type=media_type, size_bytes=size, sha256=digest.hexdigest(),
                            file_role=role, created_by_id=user.id, created_by=actor_name(user)))


def cleanup_failed_uploads(created_paths):
    # 이번 요청이 새로 만든, 아직 커밋되지 않은 파일만 정리합니다.
    for path in created_paths:
        if path.is_relative_to(config.DOCUMENT_STORAGE_ROOT.resolve()):
            path.unlink(missing_ok=True)


def iso_time(value):
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def revision_dict(row):
    return {"id": row.id, "item_id": row.item_id, "revision_code": row.revision_code,
            "sequence": row.sequence, "status": row.status, "previous_revision_id": row.previous_revision_id,
            "change_reason": row.change_reason, "note": row.note, "created_by": row.created_by,
            "created_at": iso_time(row.created_at), "activated_at": iso_time(row.activated_at),
            "superseded_at": iso_time(row.superseded_at), "retired_at": iso_time(row.retired_at), "retire_reason": row.retire_reason}


def document_dict(db, row, type_names=None):
    files = db.scalars(select(DocumentFile).where(DocumentFile.document_id == row.id).order_by(DocumentFile.id)).all()
    return {"id": row.id, "revision_id": row.revision_id, "document_type": row.document_type,
            "document_type_name": (type_names or {}).get(row.document_type, row.document_type),
            "document_no": row.document_no, "title": row.title, "document_revision": row.document_revision,
            "note": row.note, "created_by": row.created_by, "created_at": iso_time(row.created_at),
            "retired_at": iso_time(row.retired_at), "retire_reason": row.retire_reason,
            "files": [{"id": file.id, "original_name": file.original_name, "extension": file.extension,
                       "media_type": file.media_type, "size_bytes": file.size_bytes, "sha256": file.sha256,
                       "file_role": file.file_role, "created_by": file.created_by, "created_at": iso_time(file.created_at),
                       "can_preview": file.extension in {"pdf", "png", "jpg", "jpeg", "bmp"},
                       "download_url": f"/api/documents/files/{file.id}/download",
                       "preview_url": f"/api/documents/files/{file.id}/preview"} for file in files]}
