"""서버에서 미사용 만료를 검사하는 로그인 세션. 원본 토큰은 DB에 저장하지 않습니다."""
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from core.database import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_digest = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    idle_timeout_seconds = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    last_activity_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    absolute_expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime)
