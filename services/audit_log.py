import json
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Mapper

from models.audit_log import AuditLogModel


_AUDIT_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("mes_audit_context", default={})
_INSTALLED = False

_SENSITIVE_FIELDS = {"password_hash"}
_DOCUMENT_KEYS = (
    "document_no",
    "revision_code",
    "order_no",
    "po_no",
    "inbound_no",
    "outbound_no",
    "shipment_no",
    "packing_no",
    "work_order_no",
    "plan_no",
    "lot_no",
    "package_lot_no",
    "internal_lot_no",
    "part_no",
)


def set_audit_context(**values):
    return _AUDIT_CONTEXT.set(values)


def reset_audit_context(token) -> None:
    _AUDIT_CONTEXT.reset(token)


def _json_value(value: Any):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    return str(value)


def _snapshot(target) -> dict[str, Any]:
    state = inspect(target)
    result: dict[str, Any] = {}
    for column in state.mapper.columns:
        key = column.key
        if key in _SENSITIVE_FIELDS:
            result[key] = "***"
            continue
        try:
            result[key] = _json_value(getattr(target, key))
        except Exception:
            result[key] = None
    return result


def _document_no(target) -> str | None:
    for key in _DOCUMENT_KEYS:
        if hasattr(target, key):
            value = getattr(target, key, None)
            if value not in (None, ""):
                return str(value)
    return None


def _record_id(target) -> str | None:
    state = inspect(target)
    values = []
    for column in state.mapper.primary_key:
        value = getattr(target, column.key, None)
        if value is not None:
            values.append(str(value))
    return ",".join(values) if values else None


def _insert_log(connection, target, action: str, before=None, after=None, changed_fields=None) -> None:
    if isinstance(target, AuditLogModel):
        return

    context = _AUDIT_CONTEXT.get() or {}
    values = {
        "event_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "user_id": context.get("user_id"),
        "username": context.get("username") or "SYSTEM",
        "action": action,
        "table_name": target.__table__.name,
        "record_id": _record_id(target),
        "document_no": _document_no(target),
        "menu_path": context.get("menu_path"),
        "request_path": context.get("request_path"),
        "request_method": context.get("request_method"),
        "ip_address": context.get("ip_address"),
        "changed_fields": ",".join(changed_fields or []) or None,
        "before_json": json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
        "after_json": json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
    }
    connection.execute(AuditLogModel.__table__.insert().values(**values))


def _after_insert(mapper, connection, target) -> None:
    if isinstance(target, AuditLogModel):
        return
    _insert_log(connection, target, "CREATE", after=_snapshot(target))


def _after_update(mapper, connection, target) -> None:
    if isinstance(target, AuditLogModel):
        return

    state = inspect(target)
    changed = []
    after = _snapshot(target)
    before = dict(after)

    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        changed.append(attr.key)
        if history.deleted:
            before[attr.key] = _json_value(history.deleted[0])
        elif history.added and not history.deleted:
            before[attr.key] = None

    if not changed:
        return

    _insert_log(
        connection,
        target,
        "UPDATE",
        before=before,
        after=after,
        changed_fields=changed,
    )


def _after_delete(mapper, connection, target) -> None:
    if isinstance(target, AuditLogModel):
        return
    _insert_log(connection, target, "DELETE", before=_snapshot(target))


def install_audit_logging() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(Mapper, "after_insert", _after_insert)
    event.listen(Mapper, "after_update", _after_update)
    event.listen(Mapper, "after_delete", _after_delete)
    _INSTALLED = True
