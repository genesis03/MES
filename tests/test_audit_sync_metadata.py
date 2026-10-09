from datetime import datetime,timedelta
from test_production_sync import setup
from models.packing_sync import ExternalPackingRecord,PackingSourcePresence
from services import audit_log


def test_polling_skips_only_operational_updates(setup,monkeypatch):
    logs=[]
    monkeypatch.setattr(audit_log,'_insert_log',lambda *args,**kwargs:logs.append(kwargs))
    with setup.sessions() as db:
        now=datetime.now()
        row=ExternalPackingRecord(source_key='KEY',part_no='PART',lot_no='LOT',packing_date='2026-10-09',packing_qty='120',shipment_qty='0',raw_json='{}',content_hash='HASH',first_seen_at=now,last_seen_at=now,changed_at=now)
        db.add(row);db.flush();presence=PackingSourcePresence(record_id=row.id,present=True,checked_at=now);db.add(presence);db.commit()
        row.last_seen_at=now+timedelta(seconds=1)
        audit_log._after_update(None,None,row);assert not logs
        row.shipment_qty='120';audit_log._after_update(None,None,row)
        assert len(logs)==1 and 'shipment_qty' in logs[0]['changed_fields']
        logs.clear();presence.checked_at=now+timedelta(seconds=1)
        audit_log._after_update(None,None,presence);assert not logs
        presence.present=False;audit_log._after_update(None,None,presence)
        assert len(logs)==1 and 'present' in logs[0]['changed_fields']
        db.rollback()
