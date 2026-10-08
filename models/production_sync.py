"""External production records are independent of stock-changing MES entries."""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, ForeignKey, UniqueConstraint
from core.database import Base


class ProductionSyncState(Base):
    __tablename__ = 'production_sync_state'
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


class ExternalProductionRecord(Base):
    __tablename__ = 'external_production_records'
    id = Column(Integer, primary_key=True)
    source_key = Column(String(64), nullable=False, unique=True, index=True)
    lot_no = Column(String(200), nullable=False, index=True)
    job_no = Column(String(200), nullable=False)
    part_no = Column(String(200), nullable=False, index=True)
    process_name = Column(String(200), nullable=False, index=True)
    work_date = Column(String(10), nullable=False, index=True)
    started_at = Column(String(20))
    ended_at = Column(String(20))
    job_qty = Column(String(100), nullable=False)
    lot_qty = Column(String(100), nullable=False)
    fault_qty = Column(String(100), nullable=False)
    raw_json = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    changed_at = Column(DateTime, nullable=False)


class ProductionSyncRun(Base):
    __tablename__ = 'production_sync_runs'
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)
    start_date = Column(String(10), nullable=False)
    end_date = Column(String(10), nullable=False)
    trigger = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False)
    counts_json = Column(Text, nullable=False, default='{}')
    error = Column(Text)


class ProductionSyncProcessMap(Base):
    __tablename__ = 'production_sync_process_maps'
    id = Column(Integer, primary_key=True)
    source_name = Column(String(200), nullable=False, unique=True)
    process_code = Column(String(100), nullable=False)
    performance_type = Column(String(20), nullable=False, default='')


class ProductionSyncItemMap(Base):
    __tablename__ = 'production_sync_item_maps'
    __table_args__ = (UniqueConstraint('source_part_no', 'source_process_name', name='uq_sync_source_item_process'),)
    id = Column(Integer, primary_key=True)
    source_part_no = Column(String(200), nullable=False)
    source_process_name = Column(String(200), nullable=False)
    item_id = Column(Integer, ForeignKey('item_master.id'), nullable=False)
