"""Atomic daily shipment snapshots; no writes to native shipment/stock tables."""
import hashlib
import json
from datetime import timedelta
from sqlalchemy import update
from models.shipment_sync import ShipmentSyncState, ExternalShipment, ExternalShipmentLot
from services.production_sync_client import SyncError


def save_day(rows, token, queried_day, complete_day=True):
    from services import production_sync_service as sync
    keys = [r['header']['RULE_NO'] for r in rows]
    if len(keys) != len(set(keys)) or any(r['header']['OUT_DATE'] != queried_day.isoformat() for r in rows):
        raise SyncError('출고 저장일 또는 중복 전표를 확인하세요.')
    counts = dict(received=len(rows), inserted=0, updated=0, unchanged=0)
    with sync.SessionLocal() as db:
        instant = sync.now()
        renewed = db.execute(update(ShipmentSyncState).where(
            ShipmentSyncState.id == 1, ShipmentSyncState.lease_token == token,
            ShipmentSyncState.lease_until > instant).values(lease_until=instant + timedelta(minutes=10)))
        if renewed.rowcount != 1:
            raise SyncError('동기화 작업의 실행 권한이 만료되었습니다. 다시 실행해 주세요.')
        for source in rows:
            header = source['header']
            # Only parsed business fields, never login payloads or server diagnostics.
            raw = json.dumps(source, ensure_ascii=False, sort_keys=True)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            record = db.query(ExternalShipment).filter_by(source_key=header['RULE_NO']).one_or_none()
            if record is None:
                record = ExternalShipment(source_key=header['RULE_NO'], first_seen_at=instant)
                db.add(record); counts['inserted'] += 1
            elif record.content_hash == digest and record.present:
                record.last_seen_at = instant; counts['unchanged'] += 1
                continue
            else:
                counts['updated'] += 1
            record.shipment_no = header['OUT_NUM']; record.shipment_date = header['OUT_DATE']
            record.customer_name = header['TR_COMP_NM']; record.status = header['STAT_CODE']
            record.present = True; record.raw_json = raw; record.content_hash = digest
            record.changed_at = record.last_seen_at = instant
            db.flush()
            db.query(ExternalShipmentLot).filter_by(shipment_id=record.id).delete(synchronize_session=False)
            for item in source['items']:
                for lot in item['lots']:
                    db.add(ExternalShipmentLot(shipment_id=record.id, detail_key=item['RULE_NO'],
                        source_lot_key=lot['RULE_NO'], part_no=item['ITEM_NUM'], lot_no=lot['LOT_NUM'],
                        allocated_qty=lot['JOB_QTY']))
        # Only a fully fetched day can mark disappeared/cancelled source rows absent.
        if complete_day:
            for record in db.query(ExternalShipment).filter(ExternalShipment.shipment_date == queried_day.isoformat()):
                if record.source_key not in keys:
                    record.present = False
        db.commit()
    return counts
