"""Shared revision-preserving corrections; each caller enforces document access."""
import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from models.audit_log import AuditLogModel


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def correction_token(data):
    snapshot = {k: v for k, v in data.items() if k not in {"correction_token", "document_type_name"}}
    return hashlib.sha256(encoded(snapshot).encode("utf-8")).hexdigest()


def with_correction_token(data):
    return dict(data, correction_token=correction_token(data))


def validate_correction(reason, token, before):
    if not reason or not reason.strip() or len(reason.strip()) > 4000:
        raise HTTPException(422, "수정 사유를 1~4000자로 입력해 주세요.")
    if token != correction_token(before):
        raise HTTPException(409, "문서가 변경되었습니다. 다시 조회한 뒤 수정해 주세요.")


def record_correction(db, user, table, record_id, reason, before, after):
    ignored = {"correction_token", "version", "updated_at", "updated_by", "updated_by_id"}
    changed = [key for key in sorted(set(before) | set(after))
               if key not in ignored and encoded(before.get(key)) != encoded(after.get(key))]
    if not changed:
        raise HTTPException(422, "변경한 내용이 없습니다.")
    db.add(AuditLogModel(
        event_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_id=user.id, username=user.name or user.username, action="AMEND",
        table_name=table, record_id=str(record_id),
        document_no=str(after.get("document_no") or after.get("revision_code") or after.get("revision") or ""),
        changed_fields=",".join(changed),
        before_json=encoded(before), after_json=encoded({"reason": reason.strip(), "document": after}),
    ))


def correction_history(db, table, record_id):
    rows = db.query(AuditLogModel).filter(
        AuditLogModel.table_name == table, AuditLogModel.record_id == str(record_id),
        AuditLogModel.action == "AMEND",
    ).order_by(AuditLogModel.id.desc()).all()
    result = []
    for row in rows:
        after = json.loads(row.after_json or "{}")
        result.append({"id": row.id, "date": row.event_at, "user": row.username,
                       "reason": after.get("reason", ""), "fields": row.changed_fields or "",
                       "before": json.loads(row.before_json or "{}"), "after": after.get("document", {})})
    return result
