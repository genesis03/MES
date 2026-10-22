"""External subcontract inspection snapshots, separate from production defects."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from core.database import Base


class SubcontractFaultSyncState(Base):
    __tablename__ = 'subcontract_fault_sync_state'
    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    interval_minutes = Column(Integer, nullable=False, default=60)
    lookback_days = Column(Integer, nullable=False, default=7)
    next_run_at = Column(DateTime)
    lease_until = Column(DateTime)
    lease_token = Column(String(64))
    initial_completed_at = Column(DateTime)
    last_run_at = Column(DateTime)
    last_success_at = Column(DateTime)
    last_error = Column(Text)
    last_counts_json = Column(Text, nullable=False, default='{}')


class SubcontractFaultSyncRun(Base):
    __tablename__ = 'subcontract_fault_sync_runs'
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)
    start_date = Column(String(10), nullable=False)
    end_date = Column(String(10), nullable=False)
    trigger = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    counts_json = Column(Text, nullable=False, default='{}')
    error = Column(Text)


class ExternalSubcontractFault(Base):
    __tablename__ = 'external_subcontract_faults'
    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), unique=True, nullable=False, index=True)
    confirm_date = Column(String(10), nullable=False, index=True)
    inbound_no = Column(String(100), nullable=False, index=True)
    part_no = Column(String(200), nullable=False, index=True)
    lot_no = Column(String(200), nullable=False, index=True)
    process_name = Column(String(100), nullable=False)
    defect_qty = Column(String(100), nullable=False)
    present = Column(Boolean, nullable=False, default=True, index=True)
    raw_json = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    changed_at = Column(DateTime, nullable=False)


class SubcontractFaultFieldMap(Base):
    __tablename__ = 'subcontract_fault_field_maps'
    id = Column(Integer, primary_key=True)
    settings_json = Column(Text, nullable=False, default='{}')
