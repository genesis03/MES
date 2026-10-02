from fastapi import HTTPException
from sqlalchemy import select
from core.security import check_admin_permission, parse_user_permissions
from models.process_flow import ProcessFlowRevision, ProcessFlowStep
from models.models import ItemMasterModel
from services.document_service import lock_item
from services.standard_document_item_service import is_selectable_finished_item, require_finished_item

FLOW_MENU_PATH = "/standard-documents/process-flows"


def has_flow_access(user, action="READ"):
    if not user:
        return False
    if check_admin_permission(user):
        return True
    access = parse_user_permissions(user).get("menu_access")
    value = access.get(FLOW_MENU_PATH) if isinstance(access, dict) else None
    level = "READ" if value is True else str(value or "NONE").upper()
    return level == "WRITE" if action == "WRITE" else level in {"READ", "WRITE"}


def require_flow_access(user, action="READ"):
    if not has_flow_access(user, action):
        raise HTTPException(403, "공정흐름도 쓰기 권한이 필요합니다." if action == "WRITE" else "공정흐름도 조회 권한이 없습니다.")


def flow_steps(db, revision_id):
    return db.scalars(select(ProcessFlowStep).where(ProcessFlowStep.revision_id == revision_id,
        ProcessFlowStep.retired_at.is_(None)).order_by(ProcessFlowStep.sort_order, ProcessFlowStep.id)).all()


def step_dict(row):
    return {"id": row.id, "step_key_id": row.step_key_id, "sort_order": row.sort_order,
            "step_no": row.step_no, "step_name": row.step_name, "note": row.note or "",
            "symbol_code": row.symbol_code or "", "symbol_name": row.symbol_name_snapshot or "",
            "symbol_shape": row.symbol_shape_snapshot or ""}


def flow_dict(db, row, include_steps=True):
    item = db.get(ItemMasterModel, row.item_id)
    result = {
        "id": row.id, "item_id": row.item_id, "part_no": item.part_no if item else row.part_no_snapshot,
        "part_name": item.part_name if item else row.part_name_snapshot, "item_active": item.is_active if item else "N",
        "item_selectable": is_selectable_finished_item(db, item),
        "part_no_snapshot": row.part_no_snapshot, "part_name_snapshot": row.part_name_snapshot,
        "revision_code": row.revision_code, "sequence": row.sequence, "version": row.version, "status": row.status,
        "previous_revision_id": row.previous_revision_id, "change_reason": row.change_reason or "",
        "note": row.note or "", "created_by": row.created_by,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "activated_at": row.activated_at.strftime("%Y-%m-%d %H:%M:%S") if row.activated_at else "",
        "retire_reason": row.retire_reason or "",
    }
    if include_steps:
        result["steps"] = [step_dict(x) for x in flow_steps(db, row.id)]
    return result


def get_flow(db, revision_id):
    row = db.get(ProcessFlowRevision, revision_id)
    if not row:
        raise HTTPException(404, "공정흐름도 개정을 찾을 수 없습니다.")
    return row


def lock_flow(db, revision_id, version, require_active=True):
    row = get_flow(db, revision_id)
    item = lock_item(db, row.item_id, require_active=require_active)
    if require_active:
        require_finished_item(db, item)
    row = db.scalar(select(ProcessFlowRevision).where(ProcessFlowRevision.id == revision_id)
                    .with_for_update().execution_options(populate_existing=True))
    if row.version != version:
        raise HTTPException(409, "다른 사용자가 변경했습니다. 다시 조회해 주세요.")
    return item, row


def usable_flow(db, item_id, revision_id):
    row = get_flow(db, revision_id)
    if row.item_id != item_id or row.status not in {"CURRENT", "SUPERSEDED"}:
        raise HTTPException(422, "같은 품목의 적용된 공정흐름도 개정을 선택해 주세요.")
    return row
