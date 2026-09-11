import os
import json
import hmac
import secrets
import hashlib
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import Request, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.database import SessionLocal, get_db, engine

# 모델 임포트 호환성 확보
try:
    from models import UserModel
except ImportError:
    from models.models import UserModel

SECRET_KEY = os.getenv("SECRET_KEY", "unicore-cloud-secret-key-2026")

# 기본 권한 트리 템플릿 (일반 계정용)
DEFAULT_PERMISSIONS: Dict[str, Any] = {
    "shipping": {
        "enabled": True,
        "menus": {
            "history": "READ",
            "print": "READ",
            "manual": "READ",
            "verify": "READ"
        }
    }
}

LEVEL_WEIGHT = {"NONE": 0, "READ": 1, "WRITE": 2}

def ensure_database_schema():
    """기존 users 테이블에 permissions 컬럼이 없을 경우 자동 추가"""
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN permissions TEXT;"))
            conn.commit()
        except Exception:
            pass

ensure_database_schema()

def hash_password(password: str) -> str:
    """비밀번호 PBKDF2 단방향 해시 암호화"""
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
    return f"{salt}:{pwd_hash}"

def verify_password(password: str, stored_hash: str) -> bool:
    """단방향 해시 비밀번호 일치 여부 검증"""
    try:
        salt, pwd_hash = stored_hash.split(":")
        check_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
        return secrets.compare_digest(pwd_hash, check_hash)
    except Exception:
        return False

def create_session_token(username: str) -> str:
    """HMAC 기반 위변조 방지 세션 토큰 생성"""
    signature = hmac.new(SECRET_KEY.encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{username}|{signature}"

def verify_session_token(token: str) -> Optional[str]:
    """세션 토큰 검증 및 사용자명 추출"""
    if not token or "|" not in token:
        return None
    username, signature = token.split("|", 1)
    expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), username.encode("utf-8"), hashlib.sha256).hexdigest()
    if secrets.compare_digest(signature, expected_sig):
        return username
    return None

def parse_user_permissions(user: UserModel) -> Dict[str, Any]:
    """사용자의 JSON 권한을 딕셔너리로 역직렬화 (관리자 예외 및 안전 폴백)"""
    if str(user.role).strip().upper() == "ADMIN":
        return {
            "shipping": {
                "enabled": True,
                "menus": {
                    "history": "WRITE",
                    "print": "WRITE",
                    "manual": "WRITE",
                    "verify": "WRITE"
                }
            }
        }

    if not user.permissions or str(user.permissions).strip() in ("", "{}", "null"):
        return DEFAULT_PERMISSIONS

    try:
        parsed = json.loads(user.permissions)
        if "shipping" not in parsed:
            return DEFAULT_PERMISSIONS
        return parsed
    except Exception:
        return DEFAULT_PERMISSIONS

def check_permission(user: UserModel, parent: str, child: str, min_level: str = "READ") -> bool:
    """상위 메뉴 및 하위 메뉴 권한 등급을 순차 검증하는 계층형 권한 판별 함수"""
    if str(user.role).strip().upper() == "ADMIN":
        return True

    perms = parse_user_permissions(user)
    parent_cfg = perms.get(parent, {})

    # 1. 상위 메뉴 비활성화 시 즉시 거부
    if not parent_cfg.get("enabled", False):
        return False

    # 2. 하위 메뉴 권한 등급 비교
    child_level = parent_cfg.get("menus", {}).get(child, "NONE")
    return LEVEL_WEIGHT.get(child_level, 0) >= LEVEL_WEIGHT.get(min_level, 1)

def init_default_accounts():
    """초기 관리자(admin) 및 일반 사용자(user) 계정 자동 생성"""
    db = SessionLocal()
    try:
        if not db.query(UserModel).filter(UserModel.username == "admin").first():
            admin_user = UserModel(
                username="admin",
                password_hash=hash_password("admin1234"),
                role="ADMIN",
                can_write=1,
                permissions=json.dumps({
                    "shipping": {
                        "enabled": True,
                        "menus": {
                            "history": "WRITE",
                            "print": "WRITE",
                            "manual": "WRITE",
                            "verify": "WRITE"
                        }
                    }
                }, ensure_ascii=False),
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            db.add(admin_user)

        if not db.query(UserModel).filter(UserModel.username == "user").first():
            normal_user = UserModel(
                username="user",
                password_hash=hash_password("user1234"),
                role="USER",
                can_write=0,
                permissions=json.dumps(DEFAULT_PERMISSIONS, ensure_ascii=False),
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            db.add(normal_user)
        db.commit()
    finally:
        db.close()

def get_current_user_optional(request: Request, db: Session) -> Optional[UserModel]:
    """쿠키 세션을 확인하여 현재 로그인된 사용자 객체 반환"""
    token = request.cookies.get("session_token")
    if not token:
        return None
    username = verify_session_token(token)
    if not username:
        return None
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if user:
        user.perms = parse_user_permissions(user)
    return user

def require_api_user(request: Request, db: Session = Depends(get_db)) -> UserModel:
    """로그인 필수 API 엔드포인트 디펜던시"""
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="인증되지 않았습니다. 로그인해 주십시오.")
    return user

def require_admin_user(user: UserModel = Depends(require_api_user)) -> UserModel:
    """관리자(ADMIN) 전용 API 디펜던시"""
    if str(user.role).strip().upper() != "ADMIN":
        raise HTTPException(status_code=403, detail="관리자 전용 기능입니다. 접근 권한이 없습니다.")
    return user

def require_permission(parent: str, child: str, min_level: str = "READ"):
    """상하위 계층 메뉴 권한 검증 디펜던시 팩토리"""
    def dependency(user: UserModel = Depends(require_api_user)) -> UserModel:
        if not check_permission(user, parent, child, min_level):
            raise HTTPException(status_code=403, detail="해당 기능에 대한 접근 권한이 없습니다.")
        return user
    return dependency