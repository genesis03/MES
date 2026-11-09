import hashlib
import json
from datetime import timedelta
from sqlalchemy import update
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch,SubcontractDispatchSyncState
from services.subcontract_dispatch_client import unique_rows
from services.production_sync_client import SyncError


def group_key(row):
    return hashlib.sha256(json.dumps([row[k] for k in ('REQ_NUM','REQ_COMP_NO','ETC_REQ_TYPE')],ensure_ascii=False).encode()).hexdigest()


def save_range(rows,token,start,end):
    from services import production_sync_service as sync
    rows=unique_rows(rows)
    from services.subcontract_order_lots import validate_allocations
    for source in rows:validate_allocations(source)
    if start>end or any(not start.isoformat()<=r['REQ_DATE_STR']<=end.isoformat() for r in rows):raise SyncError('외주 출고 저장일과 조회 기간이 다릅니다.')
    counts=dict(received=len(rows),inserted=0,updated=0,unchanged=0)
    with sync.SessionLocal() as db:
        instant=sync.now()
        changed=db.execute(update(SubcontractDispatchSyncState).where(SubcontractDispatchSyncState.id==1,SubcontractDispatchSyncState.lease_token==token,SubcontractDispatchSyncState.lease_until>instant).values(lease_until=instant+timedelta(minutes=10)))
        if changed.rowcount!=1:raise SyncError('외주 출고 연동 실행 권한이 만료되었습니다.')
        for source in rows:
            raw=json.dumps(source,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(raw.encode()).hexdigest()
            record=db.query(ExternalSubcontractDispatch).filter_by(source_key=source['RULE_NO']).one_or_none()
            if record is None:
                record=ExternalSubcontractDispatch(source_key=source['RULE_NO'],first_seen_at=instant);db.add(record);counts['inserted']+=1
            elif record.content_hash==digest:
                record.last_seen_at=instant;counts['unchanged']+=1;continue
            else:counts['updated']+=1
            for name,field in [('outbound_no','REQ_NUM'),('outbound_date','REQ_DATE_STR'),('partner_name','REQ_COMP_NM'),('process_code','ETC_REQ_TYPE'),('process_name','ETC_REQ_TYPE_NM'),('part_no','REQ_ITEM_NUM'),('previous_part_no','ITEM_NUM'),('lot_no','LOT_NUM'),('outbound_qty','ETC_REQ_QTY'),('source_status','STAT_CODE'),('source_status_name','STAT_CODE_NM'),('storage_name','IN_WARE_NM')]:setattr(record,name,source[field])
            record.group_key=group_key(source);record.raw_json=raw;record.content_hash=digest;record.last_seen_at=record.changed_at=instant
        # Absence is not a verified cancellation signal in the legacy list.
        db.commit()
    return counts
