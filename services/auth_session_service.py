"""서버 시간 기반 세션 검사/활동 갱신. 단순 조회·폴링은 만료를 연장하지 않습니다."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import select, update
from core.config import SESSION_ADMIN_IDLE_MINUTES, SESSION_USER_IDLE_MINUTES, SESSION_COOKIE_MAX_AGE_SECONDS
from core.database import SessionLocal
from models.auth_session import AuthSession, utc_now
from models.models import UserModel

TOKEN_PATTERN = re.compile(r"s2\.[A-Za-z0-9_-]{43}")


def token_digest(token):
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def active_session(db, token):
    digest = token_digest(token)
    if not digest:
        return None
    now = utc_now()
    return db.scalar(select(AuthSession).where(
        AuthSession.token_digest == digest, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > now, AuthSession.absolute_expires_at > now
    ).execution_options(populate_existing=True))


def session_user(db, token):
    session = active_session(db, token)
    if not session:
        return None
    user = db.get(UserModel, session.user_id)
    if not user or (hasattr(user, "is_active") and not user.is_active):
        return None
    return user


def issue_token(username, db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        user = db.scalar(select(UserModel).where(UserModel.username == username))
        if not user or (hasattr(user, "is_active") and not user.is_active):
            raise HTTPException(401, "로그인이 필요합니다.")
        from core.security import check_admin_permission
        minutes = SESSION_ADMIN_IDLE_MINUTES if check_admin_permission(user) else SESSION_USER_IDLE_MINUTES
        now = utc_now()
        token = "s2." + secrets.token_urlsafe(32)
        db.add(AuthSession(token_digest=token_digest(token), user_id=user.id,
            idle_timeout_seconds=minutes * 60, created_at=now, last_activity_at=now,
            expires_at=now + timedelta(minutes=minutes),
            absolute_expires_at=now + timedelta(seconds=SESSION_COOKIE_MAX_AGE_SECONDS)))
        if own:
            db.commit()
        else:
            db.flush()
        return token
    finally:
        if own:
            db.close()


def verify_username(token, db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        user = session_user(db, token)
        return user.username if user else None
    finally:
        if own:
            db.close()


def activity_key(session):
    # 상태 API에서 받은 키를 커스텀 헤더에 보내야 합니다. 외부 사이트의 단순 요청으로 갱신하지 못합니다.
    from core.security import SECRET_KEY
    secret = SECRET_KEY
    return hmac.new(secret.encode("utf-8"), ("activity:" + session.token_digest).encode("ascii"), hashlib.sha256).hexdigest()


def session_status(db, token):
    session = active_session(db, token)
    user = session_user(db, token)
    if not session or not user:
        raise HTTPException(401, "미사용 시간이 지나 로그인 세션이 만료되었습니다.")
    now = utc_now()
    expires = min(session.expires_at, session.absolute_expires_at)
    return {
        "username": user.username, "idle_timeout_seconds": session.idle_timeout_seconds,
        "server_time_ms": int(now.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "expires_at_ms": int(expires.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "last_activity_ms": int(session.last_activity_at.replace(tzinfo=timezone.utc).timestamp() * 1000),
        "activity_key": activity_key(session),
    }


def touch_session(db, token, key):
    session = active_session(db, token)
    if not session or not session_user(db, token):
        raise HTTPException(401, "미사용 시간이 지나 로그인 세션이 만료되었습니다.")
    if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{64}", key) or not hmac.compare_digest(key, activity_key(session)):
        raise HTTPException(403, "세션 활동 확인값이 올바르지 않습니다.")
    now = utc_now()
    expires = min(session.absolute_expires_at, now + timedelta(seconds=session.idle_timeout_seconds))
    result = db.execute(update(AuthSession).where(
        AuthSession.token_digest == session.token_digest, AuthSession.revoked_at.is_(None),
        AuthSession.expires_at > now, AuthSession.absolute_expires_at > now,
        AuthSession.last_activity_at <= now
    ).values(last_activity_at=now, expires_at=expires).execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        # 다른 탭이 먼저 갱신한 경우 유효 세션을 잘못 로그아웃시키지 않습니다.
        # 실제 만료/폐기는 session_status가 401로 차단합니다.
        return session_status(db, token)
    db.commit()
    return session_status(db, token)


def revoke_session(db, token):
    digest = token_digest(token)
    if digest:
        db.execute(update(AuthSession).where(AuthSession.token_digest == digest,
            AuthSession.revoked_at.is_(None)).values(revoked_at=utc_now()))
