"""검사기준서 통합 메뉴에서도 기존 두 탭의 메뉴별 권한을 유지합니다."""
from fastapi import Depends, HTTPException, Request
from core.security import check_admin_permission, get_current_user, parse_user_permissions

INSPECTION_MENU_PATH = "/standard-documents/inspection-standards"
STANDARD_PATH = "/quality/inbound-standards"
MASTER_PATH = "/quality/inspection-items"


def inspection_level(user, path):
    if not user:
        return "NONE"
    if check_admin_permission(user):
        return "WRITE"
    permissions = parse_user_permissions(user)
    access = permissions.get("menu_access")
    if isinstance(access, dict) and access:
        value = access.get(path)
        if value is True:
            return "READ"
        level = str(value or "NONE").upper()
        return level if level in {"READ", "WRITE"} else "NONE"
    parent = permissions.get("quality")
    if parent is True:
        return "WRITE"
    if isinstance(parent, dict) and parent.get("enabled"):
        # 구형 권한의 명시적인 READ/WRITE를 우선하고 기존 quality.enabled 계정을 유지합니다.
        if parent.get("WRITE") is True:
            return "WRITE"
        if "WRITE" in parent or "READ" in parent:
            return "READ" if parent.get("READ") else "NONE"
        return "WRITE"
    return "NONE"


def inspection_context(user):
    return {"can_read_inbound_standards": inspection_level(user, STANDARD_PATH) != "NONE",
            "can_read_inspection_master": inspection_level(user, MASTER_PATH) != "NONE"}


def get_inspection_user(request: Request, user=Depends(get_current_user)):
    path = request.url.path
    target = MASTER_PATH if path.startswith("/api/quality/inspection-items") or path == MASTER_PATH else STANDARD_PATH
    write = request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    level = inspection_level(user, target)
    # 기준서 편집 화면은 공통 검사항목을 읽어야 하지만 마스터를 수정할 권한은 얻지 않습니다.
    shared_master_read = (not write and path == "/api/quality/inspection-items"
                          and inspection_level(user, STANDARD_PATH) in {"READ", "WRITE"})
    if not shared_master_read and (level != "WRITE" if write else level == "NONE"):
        raise HTTPException(403, "해당 검사기준서 탭의 쓰기 권한이 필요합니다." if write else "해당 검사기준서 탭의 조회 권한이 없습니다.")
    return user
