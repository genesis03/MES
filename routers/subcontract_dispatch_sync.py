import json
from datetime import date
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record,SubcontractDispatchSyncRun
from routers.production_sync import access,local_time

router=APIRouter(prefix='/api/subcontract/outbound/external-sync',tags=['External subcontract dispatch'])


@router.get('/runs')
def runs(db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    return [dict(started_at=local_time(r.started_at),start_date=r.start_date,end_date=r.end_date,trigger=r.trigger,status=r.status,counts=json.loads(r.counts_json),error=r.error or '') for r in db.query(SubcontractDispatchSyncRun).order_by(SubcontractDispatchSyncRun.id.desc()).limit(30)]


@router.get('/records')
def records(start_date:date,end_date:date,keyword:str=Query('',max_length=200),page:int=Query(1,ge=1),db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    if start_date>end_date:raise HTTPException(422,'시작일과 종료일을 확인하세요.')
    query=db.query(Record).filter(Record.outbound_date.between(start_date.isoformat(),end_date.isoformat()))
    if keyword.strip():query=query.filter(or_(*[c.contains(keyword.strip(),autoescape=True) for c in (Record.outbound_no,Record.part_no,Record.previous_part_no,Record.partner_name,Record.lot_no,Record.raw_json)]))
    total=query.count()
    rows=query.order_by(Record.outbound_date.desc(),Record.id.desc()).offset((page-1)*50).limit(50)
    from services.subcontract_order_lots import allocations
    return dict(total=total,page=page,rows=[dict(lot_count=len(allocations(r)),**{k:getattr(r,k) for k in ('id','outbound_no','outbound_date','partner_name','process_name','part_no','previous_part_no','lot_no','outbound_qty','source_status_name')}) for r in rows])
