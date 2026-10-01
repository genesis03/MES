"""공정 FMEA: 품목 연결, 독립 문서 개정, 분석행. 이력의 물리 삭제는 제공하지 않습니다."""
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from core.database import Base


class FmeaDocument(Base):
    __tablename__ = "fmea_documents"
    __table_args__ = (UniqueConstraint("item_id", "document_no", name="uq_fmea_item_document_no"),)
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("item_master.id", ondelete="RESTRICT"), nullable=False, index=True)
    document_no = Column(String(100), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)


class FmeaRevision(Base):
    __tablename__ = "fmea_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_code", name="uq_fmea_revision_code"),
        UniqueConstraint("document_id", "sequence", name="uq_fmea_revision_sequence"),
        CheckConstraint("status IN ('DRAFT','CURRENT','SUPERSEDED','RETIRED')"),
        CheckConstraint("sequence > 0 AND version > 0"),
        Index("uq_fmea_current", "document_id", unique=True,
              sqlite_where=text("status = 'CURRENT'"), postgresql_where=text("status = 'CURRENT'")),
    )
    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("fmea_documents.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision_code = Column(String(50), nullable=False)
    sequence = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="DRAFT")
    previous_revision_id = Column(Integer, ForeignKey("fmea_revisions.id", ondelete="RESTRICT"))
    basis_item_revision_id = Column(Integer, ForeignKey("item_revisions.id", ondelete="RESTRICT"))
    basis_revision_snapshot = Column(String(50))
    part_no_snapshot = Column(String(100), nullable=False)
    part_name_snapshot = Column(String(200), nullable=False)
    company = Column(String(200))
    model_year = Column(String(100))
    team = Column(String(200))
    prepared_by = Column(String(100), nullable=False)
    date_prepared = Column(Date, nullable=False)
    change_reason = Column(Text)
    note = Column(Text)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_at = Column(DateTime)
    activated_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    activated_by = Column(String(100))
    activated_at = Column(DateTime)
    superseded_at = Column(DateTime)
    retired_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    retired_at = Column(DateTime)
    retire_reason = Column(Text)


class FmeaRow(Base):
    __tablename__ = "fmea_rows"
    __table_args__ = (
        CheckConstraint("sort_order > 0"),
        *(CheckConstraint(f"{field} IS NULL OR ({field} >= 1 AND {field} <= 10)") for field in
          ("severity", "occurrence", "detection", "new_severity", "new_occurrence", "new_detection")),
    )
    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("fmea_revisions.id", ondelete="RESTRICT"), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False)
    # 기존 공정코드 변경 기능은 이 참조만 갱신하고 아래 인쇄용 스냅샷은 보존합니다.
    process_code = Column(String, ForeignKey("processes.process_code", ondelete="RESTRICT"), index=True)
    process_code_snapshot = Column(String(100))
    process_name_snapshot = Column(String(200))
    function_text = Column(Text)
    failure_mode = Column(Text)
    effects = Column(Text)
    severity = Column(Integer)
    classification = Column(String(50))
    causes = Column(Text)
    occurrence = Column(Integer)
    prevention_controls = Column(Text)
    detection_controls = Column(Text)
    detection = Column(Integer)
    recommended_actions = Column(Text)
    responsibility = Column(String(100))
    target_date = Column(Date)
    actions_taken = Column(Text)
    completion_date = Column(Date)
    new_severity = Column(Integer)
    new_occurrence = Column(Integer)
    new_detection = Column(Integer)
    note = Column(Text)
    retired_at = Column(DateTime)
    retired_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
