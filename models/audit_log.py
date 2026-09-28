from sqlalchemy import Column, Integer, String, Text

from core.database import Base


class AuditLogModel(Base):
    """업무 데이터 변경 이력.

    SQLite의 audit_logs 테이블에서 직접 조회할 수 있습니다.
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_at = Column(String, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String, nullable=False, default="SYSTEM", index=True)
    action = Column(String, nullable=False, index=True)  # CREATE / UPDATE / DELETE
    table_name = Column(String, nullable=False, index=True)
    record_id = Column(String, nullable=True, index=True)
    document_no = Column(String, nullable=True, index=True)
    menu_path = Column(String, nullable=True, index=True)
    request_path = Column(String, nullable=True)
    request_method = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    changed_fields = Column(Text, nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
