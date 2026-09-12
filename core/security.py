# core/security.py
import hashlib
import hmac
import json
import os
from datetime import datetime
from typing import Optional, Union, Any
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session

from core.database import get_db

# ==============================================================================
# 개발 모드 인증 우회 플래그 (배포 시 False로 변경)
# ==============================================================================
DEV_BYPASS_AUTH = True


class MockAdminUser:
    """개발 편의를 위한 가상 최고관리자 객체"""
    id = 1
    username = "admin"
    name = "최고관리자"
    role = "ADMIN"
    permissions = "{}"
    department = "관리본부"
    note = "개발 모드 무인증 통과 계정"
    is_active = True
    created_at = "2026-01-01 00:00:00"

    def __repr__(self):
        return f"<MockAdminUser {self.username}>"


# 환경변수 우선 적용 (미설정 시 개발용 기본 시크릿 유지)
SECRET_KEY = os.getenv("SECRET_KEY", "unicore-cloud-secret-key-2026")

# 기본 권한 체계 정의 (계층형 세부 권한 구조)
DEFAULT_PERMISSIONS = {
    "shipping": {
        "upload": {"READ": True, "WRITE": True},
        "verify": {"READ": True, "WRITE": True},
        "history": {"READ": True, "WRITE": True},
        "stats": {"READ": True, "WRITE": True},
    },
    "manual": {
        "print": {"READ": True, "WRITE": True},
        "history": {"READ": True, "WRITE": True},
    },
    "basic_info": {"READ": True, "WRITE": True},
    "bom": {"READ": True, "WRITE": True},
}


def hash_password(password: str) -> str:
    """PBKDF2 SHA256 단방향 비밀번호 해싱"""
    salt = "unicore_salt_2026"
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        100000
    )
    return pwd_hash.hex()


get_password_hash = hash_password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """비밀번호 일치 여부 검증"""
    return hmac.compare_digest(hash_password(plain_password), hashed_password)


def create_session_token(username: str) -> str:
    """HMAC 서명 기반 세션 토큰 생성"""
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        str(username).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{username}|{signature}"


generate_session_token = create_session_token


def verify_session_token(token: str) -> Optional[str]:
    """세션 토큰 유효성 검증 및 사용자 식별자 반환"""
    if not token or "|" not in token:
        return None
    try:
        username, signature = token.split("|", 1)
        expected_signature = hmac.new(
            SECRET_KEY.encode("utf-8"),
            username.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(signature, expected_signature):
            return username
    except Exception:
        return None
    return None


def get_current_user_optional(request: Request, db: Session = Depends(get_db)):
    """현재 로그인 사용자 조회 (스마트 경로 감지 우회)"""
    # 1. 로그인 화면 진입 또는 로그아웃 요청 시에는 무조건 우회를 끄고 비로그인(None) 처리
    req_path = request.url.path.rstrip("/")
    if req_path in ("/login", "/logout", "/api/logout"):
        return None

    # 2. 그 외 모든 업무 화면(/shipping, /admin 등)에서는 최고관리자로 자동 통과
    if DEV_BYPASS_AUTH:
        return MockAdminUser()

    # --- 배포 모드(DEV_BYPASS_AUTH = False) 정식 검증 로직 ---
    token = request.cookies.get("session_token")
    if not token:
        return None

    username = verify_session_token(token)
    if not username:
        return None

    from models.models import UserModel
    query = db.query(UserModel).filter(UserModel.username == username)
    if hasattr(UserModel, "is_active"):
        query = query.filter(UserModel.is_active == True)
    return query.first()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """현재 로그인 사용자 필수 검증"""
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다."
        )
    return user


def require_api_user(request: Request, db: Session = Depends(get_db)):
    """API 엔드포인트 전용 로그인 검증 의존성"""
    return get_current_user(request, db)


def check_admin_permission(user: Any) -> bool:
    """사용자의 관리자 권한 보유 여부 판정"""
    if DEV_BYPASS_AUTH:
        return True

    if not user:
        return False

    username = str(getattr(user, "username", "")).strip().lower()
    if username == "admin":
        return True

    role = str(getattr(user, "role", "")).strip().lower()
    if role in ("admin", "administrator", "superadmin", "system", "1", "true"):
        return True

    return False


def require_admin_user(request: Request, db: Session = Depends(get_db)):
    """관리자 권한 필수 검증"""
    user = get_current_user(request, db)
    if not check_admin_permission(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="관리자 권한이 필요합니다."
        )
    return user


def parse_permissions(permissions_data: Optional[Union[str, dict, Any]]) -> dict:
    """사용자 권한 JSON 파싱 및 기본값 병합"""
    if not permissions_data:
        return DEFAULT_PERMISSIONS.copy()
    if isinstance(permissions_data, dict):
        merged = DEFAULT_PERMISSIONS.copy()
        merged.update(permissions_data)
        return merged
    try:
        loaded = json.loads(permissions_data)
        if isinstance(loaded, dict):
            merged = DEFAULT_PERMISSIONS.copy()
            merged.update(loaded)
            return merged
    except Exception:
        pass
    return DEFAULT_PERMISSIONS.copy()


def parse_user_permissions(user_or_data: Any = None) -> dict:
    """UserModel 인스턴스 또는 권한 문자열을 받아 딕셔너리로 반환"""
    if user_or_data is None:
        return DEFAULT_PERMISSIONS.copy()
    if hasattr(user_or_data, "permissions"):
        raw_perms = getattr(user_or_data, "permissions", None)
        return parse_permissions(raw_perms)
    return parse_permissions(user_or_data)


def check_user_permission(user: Any, *args, **kwargs) -> bool:
    """모듈/메뉴/동작 권한 판정 로직"""
    if DEV_BYPASS_AUTH:
        return True

    if not user:
        return False
    if check_admin_permission(user):
        return True

    perms = parse_user_permissions(user)

    if not args and not kwargs:
        return True

    module = args[0] if len(args) > 0 else kwargs.get("module")
    feature = args[1] if len(args) > 1 else kwargs.get("feature")
    action = args[2] if len(args) > 2 else kwargs.get("action")

    if not module or module not in perms:
        return False

    module_val = perms[module]

    if isinstance(module_val, bool):
        return module_val

    if isinstance(module_val, dict):
        if not feature:
            return True
        if feature in module_val:
            feat_val = module_val[feature]
            if isinstance(feat_val, bool):
                return feat_val
            if isinstance(feat_val, dict):
                if not action:
                    return True
                return bool(feat_val.get(action, True))
        if feature in ("READ", "WRITE") and feature in module_val:
            return bool(module_val[feature])
        if action and action in module_val:
            return bool(module_val[action])

        return True

    return bool(module_val)


def check_permission(user: Any, *args, **kwargs) -> bool:
    return check_user_permission(user, *args, **kwargs)


def require_permission(*args, **kwargs):
    """FastAPI Depends 의존성 팩토리 및 직접 권한 검사 함수"""
    if args and not isinstance(args[0], str):
        user = args[0]
        perm_args = args[1:]
        if DEV_BYPASS_AUTH:
            return MockAdminUser()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="로그인이 필요합니다."
            )
        if check_admin_permission(user):
            return user
        if not check_user_permission(user, *perm_args, **kwargs):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="해당 기능에 대한 접근 권한이 없습니다."
            )
        return user

    def permission_dependency(request: Request, db: Session = Depends(get_db)):
        user = get_current_user(request, db)
        if check_admin_permission(user):
            return user
        if not check_user_permission(user, *args, **kwargs):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="해당 기능에 대한 접근 권한이 없습니다."
            )
        return user

    return permission_dependency


def init_default_accounts(db: Optional[Session] = None) -> None:
    """초기 기동 시 기본 계정 확인 및 생성"""
    close_session_at_end = False
    if db is None:
        from core.database import SessionLocal
        db = SessionLocal()
        close_session_at_end = True

    try:
        from models.models import UserModel
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        admin_user = db.query(UserModel).filter(UserModel.username == "admin").first()
        if not admin_user:
            admin_data = {
                "username": "admin",
                "password_hash": hash_password("admin1234"),
                "role": "admin",
                "created_at": now_str,
            }
            if hasattr(UserModel, "name"):
                admin_data["name"] = "관리자"
            if hasattr(UserModel, "permissions"):
                admin_data["permissions"] = json.dumps(DEFAULT_PERMISSIONS)
            if hasattr(UserModel, "is_active"):
                admin_data["is_active"] = True
            db.add(UserModel(**admin_data))

        regular_user = db.query(UserModel).filter(UserModel.username == "user").first()
        if not regular_user:
            user_data = {
                "username": "user",
                "password_hash": hash_password("user1234"),
                "role": "user",
                "created_at": now_str,
            }
            if hasattr(UserModel, "name"):
                user_data["name"] = "일반사용자"
            if hasattr(UserModel, "permissions"):
                user_data["permissions"] = json.dumps(DEFAULT_PERMISSIONS)
            if hasattr(UserModel, "is_active"):
                user_data["is_active"] = True
            db.add(UserModel(**user_data))

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        if close_session_at_end:
            db.close()