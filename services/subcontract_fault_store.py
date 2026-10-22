import hashlib
import json
from datetime import timedelta
from sqlalchemy import update
from models.subcontract_fault_sync import ExternalSubcontractFault, SubcontractFaultSyncState
from services.production_sync_client import SyncError
from services.subcontract_fault_client import IDENTITY_FIELDS


def key_for(row):
    identity = [row[field].strip().casefold() for field in IDENTITY_FIELDS]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def save_day(rows, token, queried_day):
    from services.production_sync_service import SessionLocal, now
    keys = [key_for(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise SyncError('동일 입고번호·품번·LOT·공정이 중복되어 외주불량 저장을 중단했습니다.')
    if queried_day is None or any(r['CONFIRM_DATE'] != queried_day.isoformat() for r in rows):
        raise SyncError('외주불량 저장 일자가 조회 일자와 다릅니다.')
    counts = {'received': len(rows), 'inserted': 0, 'updated': 0, 'unchanged': 0}
    with SessionLocal() as db:
        instant = now()
        changed = db.execute(update(SubcontractFaultSyncState).where(
            SubcontractFaultSyncState.id == 1, SubcontractFaultSyncState.lease_token == token,
            SubcontractFaultSyncState.lease_until > instant).values(lease_until=instant + timedelta(minutes=10)))
        if changed.rowcount != 1:
            raise SyncError('외주불량 동기화 실행 권한이 만료되었습니다.')
        existing = {r.source_key: r for r in db.query(ExternalSubcontractFault)}
        for source, key in zip(rows, keys):
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            record = existing.get(key)
            if record is None:
                record = ExternalSubcontractFault(source_key=key, first_seen_at=instant)
                db.add(record)
                counts['inserted'] += 1
            elif record.content_hash == digest and record.present:
                record.last_seen_at = instant
                counts['unchanged'] += 1
                continue
            else:
                counts['updated'] += 1
            record.confirm_date = source['CONFIRM_DATE']
            record.inbound_no = source['IN_NUM']
            record.part_no = source['ITEM_NUM']
            record.lot_no = source['LOT_NUM']
            record.process_name = source['PROC_TYPE_NM']
            record.defect_qty = source['FAULT_QTY']
            record.raw_json, record.content_hash = raw, digest
            record.present = True
            record.last_seen_at = record.changed_at = instant
        seen = set(keys)
        for record in existing.values():
            if record.present and record.confirm_date == queried_day.isoformat() and record.source_key not in seen:
                record.present = False
                record.changed_at = instant
                counts['updated'] += 1
        db.commit()
    return counts
