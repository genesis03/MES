"""품목별 공정흐름도. 단계 고유 ID는 개정 간 유지하며 번호/명칭/순서는 개정별 보존합니다."""
from datetime import datetime
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from core.database import Base


class ProcessFlowRevision(Base):
    __tablename__ = "process_flow_revisions"
    __table_args__ = (
        UniqueConstraint("item_id", "revision_code", name="uq_flow_revision_code"),
        UniqueConstraint("item_id", "sequence", name="uq_flow_sequence"),
        CheckConstraint("status IN ('DRAFT','CURRENT','SUPERSEDED','RETIRED')"),
        CheckConstraint("sequence > 0 AND version > 0"),
        Index("uq_flow_current", "item_id", unique=True,
              sqlite_where=text("status = 'CURRENT'"), postgresql_where=text("status = 'CURRENT'")),
    )
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("item_master.id", ondelete="RESTRICT"), nullable=False, index=True)
    revision_code = Column(String(50), nullable=False)
    sequence = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="DRAFT")
    previous_revision_id = Column(Integer, ForeignKey("process_flow_revisions.id", ondelete="RESTRICT"))
    part_no_snapshot = Column(String(100), nullable=False)
    part_name_snapshot = Column(Text, nullable=False)
    change_reason = Column(Text)
    note = Column(Text)
    created_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime)
    activated_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    activated_at = Column(DateTime)
    superseded_at = Column(DateTime)
    retired_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
    retired_at = Column(DateTime)
    retire_reason = Column(Text)


class ProcessFlowStepKey(Base):
    __tablename__ = "process_flow_step_keys"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("item_master.id", ondelete="RESTRICT"), nullable=False, index=True)


class ProcessFlowStep(Base):
    __tablename__ = "process_flow_steps"
    __table_args__ = (
        UniqueConstraint("revision_id", "step_key_id", name="uq_flow_step_key"),
        CheckConstraint("sort_order > 0"),
    )
    id = Column(Integer, primary_key=True)
    revision_id = Column(Integer, ForeignKey("process_flow_revisions.id", ondelete="RESTRICT"), nullable=False, index=True)
    step_key_id = Column(Integer, ForeignKey("process_flow_step_keys.id", ondelete="RESTRICT"), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False)
    step_no = Column(String(50), nullable=False)
    step_name = Column(String(200), nullable=False)
    note = Column(Text)
    retired_at = Column(DateTime)
    retired_by_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"))
