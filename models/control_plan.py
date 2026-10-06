"""Versioned control plans. Applied input and process snapshots are immutable."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, CheckConstraint, Index, text
from core.database import Base

class ControlPlanRevision(Base):
    __tablename__ = 'control_plan_revisions'
    __table_args__ = (
        UniqueConstraint('item_id', 'document_no', 'revision_code', name='uq_cp_revision'),
        CheckConstraint("status IN ('DRAFT','CURRENT','SUPERSEDED')"),
        CheckConstraint('version > 0'),
        Index('uq_cp_current', 'item_id', 'document_no', unique=True,
              sqlite_where=text("status = 'CURRENT'"), postgresql_where=text("status = 'CURRENT'")),
    )
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('item_master.id', ondelete='RESTRICT'), nullable=False, index=True)
    document_no = Column(String(100), nullable=False)
    revision_code = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default='DRAFT')
    version = Column(Integer, nullable=False, default=1)
    flow_revision_id = Column(Integer, ForeignKey('process_flow_revisions.id', ondelete='RESTRICT'), nullable=False)
    flow_version = Column(Integer, nullable=False)
    flow_snapshot_json = Column(Text, nullable=False)
    header_json = Column(Text, nullable=False)
    rows_json = Column(Text, nullable=False)
    previous_revision_id = Column(Integer, ForeignKey('control_plan_revisions.id', ondelete='RESTRICT'))
    created_by_id = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    updated_by_id = Column(Integer, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
    activated_at = Column(DateTime)
