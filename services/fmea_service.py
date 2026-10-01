"""공정 FMEA 권한/직렬화/동시 수정 방지. 파일 도면의 현재 사용 상태는 변경하지 않습니다."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from core.security import check_admin_permission, parse_user_permissions
from models.fmea import FmeaDocument, FmeaRevision, FmeaRow
from models.models import ItemMasterModel
from services.document_service import lock_item

FMEA_MENU_PATH = "/standard-documents/process-fmea"
ROW_FIELDS = (
    "process_code", "function_text", "failure_mode", "effects", "severity", "classification",
    "causes", "occurrence", "prevention_controls", "detection_controls", "detection",
    "recommended_actions", "responsibility", "target_date", "actions_taken", "completion_date",
    "new_severity", "new_occurrence", "new_detection", "note",
)
HEADER_FIELDS = ("company", "model_year", "team", "prepared_by", "date_prepared", "note")


def has_fmea_access(user, action="READ"):
    if not user:
        return False
    if check_admin_permission(user):
        return True
    access = parse_user_permissions(user).get("menu_access")
    value = access.get(FMEA_MENU_PATH) if isinstance(access, dict) else None
    level = "READ" if value is True else str(value or "NONE").upper()
    return level == "WRITE" if action == "WRITE" else level in {"READ", "WRITE"}


def require_fmea_access(user, action="READ"):
    if not has_fmea_access(user, action):
        raise HTTPException(403, "공정 FMEA 쓰기 권한이 필요합니다." if action == "WRITE" else "공정 FMEA 조회 권한이 없습니다.")


def commit_fmea(db):
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(409, "문서번호/개정번호 중복 또는 동시 변경이 발생했습니다. 새로 조회해 주세요.") from exc


def get_revision(db, revision_id):
    row = db.get(FmeaRevision, revision_id)
    if not row:
        raise HTTPException(404, "공정 FMEA 개정을 찾을 수 없습니다.")
    return row


def lock_fmea_revision(db, revision_id, version, require_active=True):
    revision = get_revision(db, revision_id)
    document = db.get(FmeaDocument, revision.document_id)
    item = lock_item(db, document.item_id, require_active=require_active)
    revision = db.scalar(select(FmeaRevision).where(FmeaRevision.id == revision_id)
                         .with_for_update().execution_options(populate_existing=True))
    if revision.version != version:
        raise HTTPException(409, "다른 사용자가 변경했습니다. 새로 조회 후 다시 작업해 주세요.")
    return item, document, revision


def row_dict(row):
    result = {field: getattr(row, field) for field in ROW_FIELDS}
    for field in ("target_date", "completion_date"):
        result[field] = result[field].isoformat() if result[field] else None
    result.update(id=row.id, sort_order=row.sort_order,
                  process_code_snapshot=row.process_code_snapshot,
                  process_name_snapshot=row.process_name_snapshot)
    for prefix in ("", "new_"):
        scores = [getattr(row, prefix + field) for field in ("severity", "occurrence", "detection")]
        result[prefix + "rpn"] = scores[0] * scores[1] * scores[2] if all(x is not None for x in scores) else None
    return result


def revision_dict(db, row, include_rows=True):
    document = db.get(FmeaDocument, row.document_id)
    item = db.get(ItemMasterModel, document.item_id)
    result = {field: getattr(row, field) or "" for field in HEADER_FIELDS if field != "date_prepared"}
    result.update(
        id=row.id, document_id=document.id, document_no=document.document_no, item_id=document.item_id,
        part_no=item.part_no if item else row.part_no_snapshot,
        part_name=item.part_name if item else row.part_name_snapshot,
        item_active=item.is_active if item else "N",
        part_no_snapshot=row.part_no_snapshot, part_name_snapshot=row.part_name_snapshot,
        revision_code=row.revision_code, sequence=row.sequence, version=row.version, status=row.status,
        basis_item_revision_id=row.basis_item_revision_id, basis_revision_snapshot=row.basis_revision_snapshot,
        previous_revision_id=row.previous_revision_id, change_reason=row.change_reason or "",
        date_prepared=row.date_prepared.isoformat(), created_by=row.created_by,
        created_at=row.created_at.isoformat(sep=" ", timespec="seconds"),
        activated_by=row.activated_by or "",
        activated_at=row.activated_at.isoformat(sep=" ", timespec="seconds") if row.activated_at else "",
        retire_reason=row.retire_reason or "",
    )
    if include_rows:
        result["rows"] = [row_dict(x) for x in db.scalars(select(FmeaRow).where(
            FmeaRow.revision_id == row.id, FmeaRow.retired_at.is_(None)
        ).order_by(FmeaRow.sort_order, FmeaRow.id))]
    return result
