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
from models.production_sync import ExternalProductionRecord, ProductionSyncRun, ProductionSyncState, ProductionSyncFailureData
from services.production_sync_credentials import CredentialError, read_credentials
from services.production_sync_client import ProductionClient, SyncError
from services.lot_uniqueness import prepare_import

_LOG = logging.getLogger(__name__)
_stop = threading.Event()
_scheduler = None


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today():
    return datetime.now(timezone(timedelta(hours=9))).date()


def credentials_ready(db=None):
    try:
        user, password = read_credentials(db)
        return bool(user and password)
    except CredentialError:
        return False


def backend(kind):
    if kind == 'subcontract':
        from models.subcontract_fault_sync import SubcontractFaultSyncState, SubcontractFaultSyncRun, ExternalSubcontractFault
        from services.subcontract_fault_client import SubcontractFaultClient
        return SubcontractFaultSyncState, SubcontractFaultSyncRun, ExternalSubcontractFault, SubcontractFaultClient
    if kind == 'shipment':
        from models.shipment_sync import ShipmentSyncState, ShipmentSyncRun, ExternalShipment
        from services.shipment_sync_client import ShipmentClient
        return ShipmentSyncState, ShipmentSyncRun, ExternalShipment, ShipmentClient
    if kind == 'packing':
        from models.packing_sync import PackingSyncState, PackingSyncRun, ExternalPackingRecord
        from services.packing_sync_client import PackingClient
        return PackingSyncState, PackingSyncRun, ExternalPackingRecord, PackingClient
    if kind != 'production':
        raise ValueError('Unknown synchronization kind')
    return ProductionSyncState, ProductionSyncRun, ExternalProductionRecord, ProductionClient


def state(db, kind='production'):
    State, _, _, _ = backend(kind)
    row = db.get(State, 1)
    if row is None:
        row = State(id=1, enabled=False, interval_minutes=60, lookback_days=7, last_counts_json='{}')
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        row = db.get(State, 1)
    return row


def claim(start, end, trigger, kind='production'):
    State, Run, _, _ = backend(kind)
    if not credentials_ready():
        raise SyncError('관리자 메뉴에서 연동 계정을 먼저 설정해 주세요.')
    with SessionLocal() as db:
        row = state(db, kind)
        instant = now()
        if trigger in {'AUTO', 'INITIAL'} and (not row.enabled or row.next_run_at and row.next_run_at > instant):
            return None
        token = uuid.uuid4().hex
        conditions = [State.id == 1,
                      or_(State.lease_until.is_(None), State.lease_until < instant)]
        if trigger in {'AUTO', 'INITIAL'}:
            conditions += [State.enabled.is_(True),
                           or_(State.next_run_at.is_(None), State.next_run_at <= instant)]
        changed = db.execute(update(State).where(*conditions).values(
            lease_until=instant + timedelta(minutes=10), lease_token=token, last_run_at=instant,
            next_run_at=instant + timedelta(minutes=row.interval_minutes), last_error=None))
        if changed.rowcount != 1:
            db.rollback()
            return None
        db.query(Run).filter(Run.status == 'RUNNING').update({
            Run.status: 'FAILED', Run.finished_at: instant,
            Run.error: '이전 실행이 종료되거나 실행 권한이 만료되었습니다. 같은 기간을 다시 조회해 주세요.'})
        run = Run(started_at=instant, start_date=start.isoformat(), end_date=end.isoformat(),
                                trigger=trigger, status='RUNNING', counts_json='{}')
        db.add(run); db.flush(); run_id = run.id; db.commit()
        return token, run_id


def key_for(row, kind='production'):
    identity = ([row['ITEM_NUM'].strip().upper(), row['LOT_NUM'].strip().upper()] if kind == 'packing'
                else [row['LOT_NUM'].strip().upper()])
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def save_day(rows, token, kind='production', queried_day=None):
    if kind == 'shipment':
        from services.shipment_sync_store import save_day as save_shipments
        return save_shipments(rows, token, queried_day)
    if kind == 'subcontract':
        from services.subcontract_fault_store import save_day as save_subcontract_day
        return save_subcontract_day(rows, token, queried_day)
    State, _, Record, _ = backend(kind)
    counts = {'received': len(rows), 'inserted': 0, 'updated': 0, 'unchanged': 0}
    keys = [key_for(row, kind) for row in rows]
    if len(keys) != len(set(keys)):
        raise SyncError('같은 품번·LOT의 포장 내역이 여러 건 있어 저장을 중단했습니다.' if kind == 'packing'
                        else '품번·작업번호·공정과 관계없이 생산 LOT가 중복되어 저장을 중단했습니다.')
    with SessionLocal() as db:
        # Renew and lock the lease before touching records; late workers cannot write.
        instant = now()
        renewed = db.execute(update(State).where(
            State.id == 1, State.lease_token == token,
            State.lease_until > instant).values(lease_until=instant + timedelta(minutes=10)))
        if renewed.rowcount != 1:
            raise SyncError('동기화 작업의 실행 권한이 만료되었습니다. 다시 실행해 주세요.')
        existing = prepare_import(db, rows, kind, Record, key_for)
        for key, source in zip(keys, rows):
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            record = existing.get(key)
            if record is not None:
                record.source_key = key
            if record is None:
                record = Record(source_key=key, first_seen_at=instant)
                db.add(record); counts['inserted'] += 1
            elif record.content_hash == digest:
                record.last_seen_at = instant; counts['unchanged'] += 1
                continue
            else:
                counts['updated'] += 1
            if kind == 'packing':
                record.part_no = source['ITEM_NUM']; record.lot_no = source['LOT_NUM'].strip().upper()
                record.packing_date = datetime.strptime(source['CREATE_DATE'], '%Y%m%d').date().isoformat()
                record.packing_qty = source['LOT_QTY']; record.shipment_qty = source['JOB_QTY']
                record.shipment_date = (datetime.strptime(source['CONFIRM_DATE'], '%Y%m%d').date().isoformat()
                                        if source['CONFIRM_DATE'] else '')
                record.customer_name = source['APPLY_COMP_NM']
            else:
                record.lot_no = source['LOT_NUM'].strip().upper(); record.job_no = source['JOB_NUM']
                record.part_no = source['ITEM_NUM']; record.process_name = source['PROC_TYPE_NM']
                record.work_date = datetime.strptime(source['JOB_TIME'], '%Y%m%d').date().isoformat()
                record.started_at = source['JOB_ST_TIME']; record.ended_at = source['JOB_END_TIME']
                record.job_qty = source['JOB_QTY']; record.lot_qty = source['LOT_QTY']; record.fault_qty = source['FAULT_QTY']
            record.raw_json = raw; record.content_hash = digest
            record.last_seen_at = instant; record.changed_at = instant
        if kind == 'packing' and queried_day is not None:
            from models.packing_sync import PackingSourcePresence
            db.flush()
            seen = set(keys)
            for record in db.query(Record).filter(Record.packing_date == queried_day.isoformat()):
                presence = db.get(PackingSourcePresence, record.id)
                if presence is None:
                    presence = PackingSourcePresence(record_id=record.id)
                    db.add(presence)
                presence.present = record.source_key in seen
                presence.checked_at = instant
        db.commit()
    return counts


def renew_query_lease(State,token):
    with SessionLocal() as db:
        instant=now()
        changed=db.execute(update(State).where(State.id==1,State.lease_token==token,State.lease_until>instant).values(lease_until=instant+timedelta(minutes=10)))
        if changed.rowcount!=1:raise SyncError('동기화 작업의 실행 권한이 만료되었습니다. 다시 실행해 주세요.')
        db.commit()


def execute(start, end, token, run_id, kind='production'):
    State, Run, Record, Client = backend(kind)
    counts = {'received': 0, 'inserted': 0, 'updated': 0, 'unchanged': 0, 'completed_days': 0, 'warnings': []}
    querying_day = None
    error = None
    failure_data = None
    partial_errors = []
    try:
        username, password = read_credentials()
        client = Client(username, password)
        password = ''
        client.on_query_progress=lambda:renew_query_lease(State,token)
        days = {start + timedelta(days=offset) for offset in range((end - start).days + 1)}
        if kind in ('packing', 'subcontract', 'shipment'):
            with SessionLocal() as db:
                run = db.get(Run, run_id)
                if run and run.trigger == 'AUTO':
                    # Packing queries filter CREATE_DATE, not shipment date.
                    # Revisit all known dates, including previously shipped lots:
                    # a later shipment cancellation can restore old stock too.
                    # Subcontract inspection cancellations also change old dates.
                    date_column = (Record.packing_date if kind == 'packing' else
                                   Record.shipment_date if kind == 'shipment' else Record.confirm_date)
                    for (packing_date,) in db.query(date_column).filter(
                            date_column < start.isoformat()).distinct():
                        days.add(datetime.strptime(packing_date, '%Y-%m-%d').date())
        for day in sorted(days):
            querying_day = day
            if kind=='production':
                client.query_errors=[]
                client.review_items=[]
            try:
                rows, warnings = client.fetch_day(day)
                result = save_day(rows, token) if kind == 'production' else save_day(rows, token, kind, queried_day=day)
            except SyncError as exc:
                if kind!='production':raise
                renew_query_lease(State,token)
                partial_errors.append(f'{day.isoformat()} 조회: {exc}')
                failure_data=failure_data or getattr(exc,'failure_data',None)
                continue
            if kind=='production' and getattr(client,'review_items',[]):
                counts.setdefault('review_items',[]).extend(client.review_items)
            for key in result:
                counts[key] += result[key]
            query_errors=getattr(client,'query_errors',[]) if kind=='production' else []
            for failed in query_errors:
                partial_errors.append(failed['error'])
                failure_data=failure_data or failed.get('failure_data')
            if not query_errors:counts['completed_days'] += 1
            counts['warnings'] = sorted(set(counts['warnings'] + warnings))
        if partial_errors:
            counts['failed_queries']=len(partial_errors)
            error=' / '.join(partial_errors)
        if not partial_errors and not counts['received'] and ((end - start).days >= 30 or (start.month, start.day) == (1, 1)):
            querying_day = None
            raise SyncError('전체 기간에서 수신한 실적이 0건입니다. 조회 조건과 계정 권한을 확인해야 하므로 가져오기 완료로 확정하지 않습니다.')
    except (SyncError, CredentialError) as exc:
        if kind=='production':failure_data=getattr(exc,'failure_data',None)
        error = f'{querying_day.isoformat()} 조회: {exc}' if querying_day else str(exc)
    except Exception:
        # Do not log raw responses or authentication payloads.
        error = '동기화 저장 처리에 실패했습니다. DB 연결과 서버 설정을 확인해 주세요.'
        _LOG.error('Production synchronization failed; run_id=%s', run_id)
    finally:
        with SessionLocal() as db:
            instant = now()
            # Atomic token check prevents a late worker from releasing a newer lease.
            run = db.get(Run, run_id)
            values = {'lease_token': None, 'lease_until': None, 'last_error': error,
                      'last_counts_json': json.dumps(counts, ensure_ascii=False)}
            if not error:
                values['last_success_at'] = instant
                if run and run.trigger == 'INITIAL':
                    values['initial_completed_at'] = instant
            db.execute(update(State).where(
                State.id == 1, State.lease_token == token).values(**values))
            if run and run.status == 'RUNNING':
                run.finished_at = instant; run.status = 'FAILED' if error else 'SUCCESS'
                run.error = error; run.counts_json = json.dumps(counts, ensure_ascii=False)
                if failure_data:
                    saved=db.get(ProductionSyncFailureData,run_id)
                    if saved is None:db.add(ProductionSyncFailureData(run_id=run_id,data_json=json.dumps(failure_data,ensure_ascii=False)))
            db.commit()


def start_manual(start, end, kind='production'):
    lease = claim(start, end, 'MANUAL', kind)
    if lease is None:
        raise SyncError('이미 동기화가 진행 중입니다.')
    threading.Thread(target=execute, args=(start, end, *lease, kind), daemon=True).start()
    return lease[1]


def schedule_cycle(kind='production'):
    with SessionLocal() as db:
        settings = state(db, kind)
        if not settings.enabled:
            return
        days = settings.lookback_days
        initial = settings.initial_completed_at is None
    end = today()
    start = end.replace(month=1, day=1) if initial else end - timedelta(days=days - 1)
    lease = claim(start, end, 'INITIAL' if initial else 'AUTO', kind)
    if lease:
        execute(start, end, *lease, kind)


def loop():
    while not _stop.wait(30):
        if not credentials_ready():
            continue
        for kind in ('production', 'packing', 'subcontract', 'shipment'):
            try:
                schedule_cycle(kind)
            except Exception:
                _LOG.error('Synchronization scheduler could not complete its cycle; kind=%s', kind)


def start_scheduler():
    global _scheduler
    if _scheduler is None or not _scheduler.is_alive():
        _stop.clear()
        _scheduler = threading.Thread(target=loop, name='production-sync', daemon=True)
        _scheduler.start()


def stop_scheduler():
    _stop.set()
