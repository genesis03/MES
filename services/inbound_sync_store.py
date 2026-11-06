import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import update
from models.inbound_sync import ExternalInboundRecord,InboundSyncState
from services.inbound_sync_client import is_test_partner
from services.production_sync_client import SyncError


def key_for(row):
    return hashlib.sha256(json.dumps([row[k].strip().casefold() for k in ('HIS_NUM','ITEM_NUM')],ensure_ascii=False).encode()).hexdigest()


def group_rows(rows):
    groups={}
    for row in rows:
        if is_test_partner(row['REQ_COMP_NM']):continue
        key=key_for(row)
        if key in groups:
            existing=groups[key]
            if any(existing[k]!=row[k] for k in row if k!='CONFIRM_QTY'):raise SyncError('같은 입고 이력·품번의 행 연결이 모호합니다.')
            existing['CONFIRM_QTY']=str(Decimal(existing['CONFIRM_QTY'])+Decimal(row['CONFIRM_QTY']))
        else:groups[key]=dict(row)
    return groups


def save_day(rows,token,queried_day):
    if queried_day is None:raise SyncError('입고 조회일이 없습니다.')
    return save_range(rows,token,queried_day,queried_day)


def save_range(rows,token,start,end,complete_range=True):
    from services import production_sync_service as sync
    if start>end or any(not start.isoformat()<=r['IN_DATE']<=end.isoformat() for r in rows):raise SyncError('입고 저장일과 조회 기간이 다릅니다.')
    groups=group_rows(rows)
    counts=dict(received=len(groups),inserted=0,updated=0,unchanged=0)
    with sync.SessionLocal() as db:
        instant=sync.now()
        changed=db.execute(update(InboundSyncState).where(InboundSyncState.id==1,InboundSyncState.lease_token==token,InboundSyncState.lease_until>instant).values(lease_until=instant+timedelta(minutes=10)))
        if changed.rowcount!=1:raise SyncError('입고 연동 실행 권한이 만료되었습니다.')
        for key,source in groups.items():
            raw=json.dumps(source,ensure_ascii=False,sort_keys=True);digest=hashlib.sha256(raw.encode()).hexdigest()
            record=db.query(ExternalInboundRecord).filter_by(source_key=key).one_or_none()
            if record is None:
                record=ExternalInboundRecord(source_key=key,first_seen_at=instant);db.add(record);counts['inserted']+=1
            elif record.content_hash==digest and record.present:
                record.last_seen_at=instant;counts['unchanged']+=1;continue
            else:counts['updated']+=1
            for name,field in [('inbound_no','IN_NUM'),('history_no','HIS_NUM'),('part_no','ITEM_NUM'),('inbound_date','IN_DATE'),('confirm_date','CONFIRM_DATE'),('partner_name','REQ_COMP_NM'),('confirmed_qty','CONFIRM_QTY'),('product_type','PRODUCT_TYPE_NM'),('material_type','MATERIAL_TYPE_NM')]:setattr(record,name,source[field])
            record.raw_json=raw;record.content_hash=digest;record.present=True;record.last_seen_at=record.changed_at=instant
        db.flush()
        if complete_range:
            for record in db.query(ExternalInboundRecord).filter(ExternalInboundRecord.inbound_date.between(start.isoformat(),end.isoformat())):
                if record.source_key not in groups:record.present=False
        db.commit()
    return counts
