"""Scheduled read-only imports; DB leases serialize manual and automatic runs."""
import hashlib
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from core import config
from core.database import SessionLocal
from models.production_sync import ExternalProductionRecord, ProductionSyncRun, ProductionSyncState
from services.production_sync_client import ProductionClient, SyncError

_LOG = logging.getLogger(__name__)
_stop = threading.Event()
_scheduler = None


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today():
    return datetime.now(timezone(timedelta(hours=9))).date()


def credentials_ready():
    return bool(config.PRODUCTION_SYNC_USER and config.PRODUCTION_SYNC_PASSWORD)


def state(db):
    row = db.get(ProductionSyncState, 1)
    if row is None:
        row = ProductionSyncState(id=1, enabled=False, interval_minutes=60, lookback_days=7, last_counts_json='{}')
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        row = db.get(ProductionSyncState, 1)
    return row


def claim(start, end, trigger):
    if not credentials_ready():
        raise SyncError('서버에 PRODUCTION_SYNC_USER와 PRODUCTION_SYNC_PASSWORD 설정이 필요합니다.')
    with SessionLocal() as db:
        row = state(db)
        instant = now()
        if trigger in {'AUTO', 'INITIAL'} and (not row.enabled or row.next_run_at and row.next_run_at > instant):
            return None
        token = uuid.uuid4().hex
        conditions = [ProductionSyncState.id == 1,
                      or_(ProductionSyncState.lease_until.is_(None), ProductionSyncState.lease_until < instant)]
        if trigger in {'AUTO', 'INITIAL'}:
            conditions += [ProductionSyncState.enabled.is_(True),
                           or_(ProductionSyncState.next_run_at.is_(None), ProductionSyncState.next_run_at <= instant)]
        changed = db.execute(update(ProductionSyncState).where(*conditions).values(
            lease_until=instant + timedelta(minutes=10), lease_token=token, last_run_at=instant,
            next_run_at=instant + timedelta(minutes=row.interval_minutes), last_error=None))
        if changed.rowcount != 1:
            db.rollback()
            return None
        db.query(ProductionSyncRun).filter(ProductionSyncRun.status == 'RUNNING').update({
            ProductionSyncRun.status: 'FAILED', ProductionSyncRun.finished_at: instant,
            ProductionSyncRun.error: '이전 실행이 종료되거나 실행 권한이 만료되었습니다. 같은 기간을 다시 조회해 주세요.'})
        run = ProductionSyncRun(started_at=instant, start_date=start.isoformat(), end_date=end.isoformat(),
                                trigger=trigger, status='RUNNING', counts_json='{}')
        db.add(run); db.flush(); run_id = run.id; db.commit()
        return token, run_id


def key_for(row):
    # Provisional identity: edits to quantities/times keep the same source key.
    identity = [row['LOT_NUM'], row['JOB_NUM'], row['PROC_TYPE_NM']]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def save_day(rows, token):
    counts = {'received': len(rows), 'inserted': 0, 'updated': 0, 'unchanged': 0}
    keys = [key_for(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise SyncError('같은 LOT·작업번호·공정의 실적이 여러 건 있어 자동 저장을 중단했습니다.')
    with SessionLocal() as db:
        # Renew and lock the lease before touching records; late workers cannot write.
        instant = now()
        renewed = db.execute(update(ProductionSyncState).where(
            ProductionSyncState.id == 1, ProductionSyncState.lease_token == token,
            ProductionSyncState.lease_until > instant).values(lease_until=instant + timedelta(minutes=10)))
        if renewed.rowcount != 1:
            raise SyncError('동기화 작업의 실행 권한이 만료되었습니다. 다시 실행해 주세요.')
        existing = {}
        for offset in range(0, len(keys), 400):
            for record in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.source_key.in_(keys[offset:offset+400])):
                existing[record.source_key] = record
        for key, source in zip(keys, rows):
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            record = existing.get(key)
            if record is None:
                record = ExternalProductionRecord(source_key=key, first_seen_at=instant)
                db.add(record); counts['inserted'] += 1
            elif record.content_hash == digest:
                record.last_seen_at = instant; counts['unchanged'] += 1
                continue
            else:
                counts['updated'] += 1
            record.lot_no = source['LOT_NUM']; record.job_no = source['JOB_NUM']
            record.part_no = source['ITEM_NUM']; record.process_name = source['PROC_TYPE_NM']
            record.work_date = datetime.strptime(source['JOB_TIME'], '%Y%m%d').date().isoformat()
            record.started_at = source['JOB_ST_TIME']; record.ended_at = source['JOB_END_TIME']
            record.job_qty = source['JOB_QTY']; record.lot_qty = source['LOT_QTY']; record.fault_qty = source['FAULT_QTY']
            record.raw_json = raw; record.content_hash = digest
            record.last_seen_at = instant; record.changed_at = instant
        db.commit()
    return counts


def execute(start, end, token, run_id):
    counts = {'received': 0, 'inserted': 0, 'updated': 0, 'unchanged': 0, 'completed_days': 0, 'warnings': []}
    error = None
    try:
        client = ProductionClient(config.PRODUCTION_SYNC_USER, config.PRODUCTION_SYNC_PASSWORD)
        day = start
        while day <= end:
            rows, warnings = client.fetch_day(day)
            result = save_day(rows, token)
            for key in result:
                counts[key] += result[key]
            counts['completed_days'] += 1
            counts['warnings'] = sorted(set(counts['warnings'] + warnings))
            day += timedelta(days=1)
    except SyncError as exc:
        error = str(exc)
    except Exception:
        # Do not log raw responses or authentication payloads.
        error = '동기화 저장 처리에 실패했습니다. DB 연결과 서버 설정을 확인해 주세요.'
        _LOG.error('Production synchronization failed; run_id=%s', run_id)
    finally:
        with SessionLocal() as db:
            instant = now()
            # Atomic token check prevents a late worker from releasing a newer lease.
            run = db.get(ProductionSyncRun, run_id)
            values = {'lease_token': None, 'lease_until': None, 'last_error': error,
                      'last_counts_json': json.dumps(counts, ensure_ascii=False)}
            if not error:
                values['last_success_at'] = instant
                if run and run.trigger == 'INITIAL':
                    values['initial_completed_at'] = instant
            db.execute(update(ProductionSyncState).where(
                ProductionSyncState.id == 1, ProductionSyncState.lease_token == token).values(**values))
            if run and run.status == 'RUNNING':
                run.finished_at = instant; run.status = 'FAILED' if error else 'SUCCESS'
                run.error = error; run.counts_json = json.dumps(counts, ensure_ascii=False)
            db.commit()


def start_manual(start, end):
    lease = claim(start, end, 'MANUAL')
    if lease is None:
        raise SyncError('이미 동기화가 진행 중입니다.')
    threading.Thread(target=execute, args=(start, end, *lease), daemon=True).start()
    return lease[1]


def loop():
    while not _stop.wait(30):
        if not credentials_ready():
            continue
        try:
            with SessionLocal() as db:
                settings = state(db)
                if not settings.enabled:
                    continue
                days = settings.lookback_days
                initial = settings.initial_completed_at is None
            end = today()
            start = end.replace(month=1, day=1) if initial else end - timedelta(days=days - 1)
            lease = claim(start, end, 'INITIAL' if initial else 'AUTO')
            if lease:
                execute(start, end, *lease)
        except Exception:
            _LOG.error('Production synchronization scheduler could not complete its cycle.')


def start_scheduler():
    global _scheduler
    if _scheduler is None or not _scheduler.is_alive():
        _stop.clear()
        _scheduler = threading.Thread(target=loop, name='production-sync', daemon=True)
        _scheduler.start()


def stop_scheduler():
    _stop.set()
