"""External subcontract dispatches, independent of native stock transactions."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from core.database import Base


class SubcontractDispatchSyncState(Base):
    __tablename__='subcontract_dispatch_sync_state'
    id=Column(Integer,primary_key=True)
    enabled=Column(Boolean,nullable=False,default=False)
    interval_minutes=Column(Integer,nullable=False,default=60)
    lookback_days=Column(Integer,nullable=False,default=7)
    next_run_at=Column(DateTime)
    lease_until=Column(DateTime)
    lease_token=Column(String(64))
    initial_completed_at=Column(DateTime)
    last_run_at=Column(DateTime)
    last_success_at=Column(DateTime)
    last_error=Column(Text)
    last_counts_json=Column(Text,nullable=False,default='{}')


class SubcontractDispatchSyncRun(Base):
    __tablename__='subcontract_dispatch_sync_runs'
    id=Column(Integer,primary_key=True)
    started_at=Column(DateTime,nullable=False)
    finished_at=Column(DateTime)
    start_date=Column(String(10),nullable=False)
    end_date=Column(String(10),nullable=False)
    trigger=Column(String(20),nullable=False)
    status=Column(String(20),nullable=False)
    counts_json=Column(Text,nullable=False,default='{}')
    error=Column(Text)


class ExternalSubcontractDispatch(Base):
    __tablename__='external_subcontract_dispatches'
    id=Column(Integer,primary_key=True)
    source_key=Column(String(100),nullable=False,unique=True,index=True)
    group_key=Column(String(64),nullable=False,index=True)
    outbound_no=Column(String(100),nullable=False,index=True)
    outbound_date=Column(String(10),nullable=False,index=True)
    partner_name=Column(String(200),nullable=False)
    process_code=Column(String(100),nullable=False)
    process_name=Column(String(100),nullable=False)
    part_no=Column(String(200),nullable=False,index=True)
    previous_part_no=Column(String(200),nullable=False)
    lot_no=Column(String(100),nullable=False,index=True)
    outbound_qty=Column(String(100),nullable=False)
    source_status=Column(String(100),nullable=False)
    source_status_name=Column(String(100),nullable=False)
    storage_name=Column(String(200),nullable=False,default='')
    raw_json=Column(Text,nullable=False)
    content_hash=Column(String(64),nullable=False)
    first_seen_at=Column(DateTime,nullable=False)
    last_seen_at=Column(DateTime,nullable=False)
    changed_at=Column(DateTime,nullable=False)
