"""품목 → Revision → 문서 → 파일. 원본과 구버전은 삭제하지 않습니다."""
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text

from core.database import Base


class ItemRevision(Base):
    __tablename__ = "item_revisions"
    __table_args__ = (
        UniqueConstraint("item_id", "revision_code", name="uq_item_revision_code"),
        UniqueConstraint("item_id", "sequence", name="uq_item_revision_sequence"),
        CheckConstraint("status IN ('DRAFT','CURRENT','SUPERSEDED','RETIRED')"),
        CheckConstraint("sequence > 0"),
        Index("uq_item_revision_current", "item_id", unique=True,
              sqlite_where=text("status = 'CURRENT'"), postgresql_where=text("status = 'CURRENT'")),
    )

    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("item_master.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision_code = Column(String(50), nullable=False)
    sequence = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="DRAFT")
    previous_revision_id = Column(Integer, ForeignKey("item_revisions.id", ondelete="RESTRICT"))
    change_reason = Column(Text)
    eco_no = Column(String(100))
    note = Column(Text)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    activated_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    activated_at = Column(DateTime)
    superseded_at = Column(DateTime)
    retired_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    retired_at = Column(DateTime)
    retire_reason = Column(Text)


class ItemDocument(Base):
    __tablename__ = "item_documents"

    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("item_revisions.id", ondelete="RESTRICT"), nullable=False, index=True)
    document_type = Column(String(50), nullable=False, index=True)
    document_no = Column(String(100), index=True)
    title = Column(String(200), nullable=False)
    document_revision = Column(String(50))
    note = Column(Text)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    retired_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    retired_at = Column(DateTime)
    retire_reason = Column(Text)


class DocumentFile(Base):
    __tablename__ = "document_files"
    __table_args__ = (CheckConstraint("size_bytes > 0"),)

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("item_documents.id", ondelete="RESTRICT"), nullable=False, index=True)
    original_name = Column(String(255), nullable=False)
    relative_path = Column(String(500), nullable=False, unique=True)
    extension = Column(String(20), nullable=False)
    media_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    file_role = Column(String(20), nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
