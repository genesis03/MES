"""External confirmed receipts, independent of native purchase/stock transactions."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from core.database import Base


class InboundSyncState(Base):
    __tablename__='inbound_sync_state'
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


class InboundSyncRun(Base):
    __tablename__='inbound_sync_runs'
    id=Column(Integer,primary_key=True)
    started_at=Column(DateTime,nullable=False)
    finished_at=Column(DateTime)
    start_date=Column(String(10),nullable=False)
    end_date=Column(String(10),nullable=False)
    trigger=Column(String(20),nullable=False)
    status=Column(String(20),nullable=False)
    counts_json=Column(Text,nullable=False,default='{}')
    error=Column(Text)


class ExternalInboundRecord(Base):
    __tablename__='external_inbound_records'
    id=Column(Integer,primary_key=True)
    source_key=Column(String(64),nullable=False,unique=True,index=True)
    inbound_no=Column(String(100),nullable=False,index=True)
    history_no=Column(String(100),nullable=False)
    part_no=Column(String(200),nullable=False,index=True)
    inbound_date=Column(String(10),nullable=False,index=True)
    confirm_date=Column(String(10),nullable=False)
    partner_name=Column(String(200),nullable=False)
    confirmed_qty=Column(String(100),nullable=False)
    product_type=Column(String(100),nullable=False,default='')
    material_type=Column(String(100),nullable=False,default='')
    present=Column(Boolean,nullable=False,default=True,index=True)
    raw_json=Column(Text,nullable=False)
    content_hash=Column(String(64),nullable=False)
    first_seen_at=Column(DateTime,nullable=False)
    last_seen_at=Column(DateTime,nullable=False)
    changed_at=Column(DateTime,nullable=False)
