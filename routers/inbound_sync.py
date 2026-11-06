import json
from datetime import date
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from models.inbound_sync import ExternalInboundRecord,InboundSyncRun
from models.models import ItemMasterModel
from routers.production_sync import access,local_time
from services import production_sync_service as sync

router=APIRouter(tags=['Inbound synchronization'])
BASE='/api/inbound/external-sync'


@router.get(BASE+'/runs')
def runs(db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    return [{'started_at':local_time(r.started_at),'start_date':r.start_date,'end_date':r.end_date,'trigger':r.trigger,'status':r.status,'counts':json.loads(r.counts_json),'error':r.error or ''} for r in db.query(InboundSyncRun).order_by(InboundSyncRun.id.desc()).limit(30)]


@router.get(BASE+'/records')
def records(start_date:date,end_date:date,keyword:str=Query('',max_length=200),product_type:str=Query('',max_length=100),page:int=Query(1,ge=1),include_missing:bool=False,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    if start_date>end_date:raise HTTPException(422,'시작일과 종료일을 확인하세요.')
    query=db.query(ExternalInboundRecord).filter(ExternalInboundRecord.inbound_date.between(start_date.isoformat(),end_date.isoformat()))
    if not include_missing:query=query.filter(ExternalInboundRecord.present.is_(True))
    if product_type:query=query.filter(ExternalInboundRecord.product_type==product_type)
    from sqlalchemy import or_
    if keyword.strip():
        query=query.filter(or_(*[c.contains(keyword.strip(),autoescape=True) for c in (ExternalInboundRecord.part_no,ExternalInboundRecord.partner_name,ExternalInboundRecord.inbound_no)]))
    total=query.count();rows=query.order_by(ExternalInboundRecord.inbound_date.desc(),ExternalInboundRecord.id.desc()).offset((page-1)*50).limit(50).all()
    items={}
    for item in db.query(ItemMasterModel):items.setdefault(item.part_no.casefold(),[]).append(item)
    result=[]
    for row in rows:
        matches=items.get(row.part_no.casefold(),[]);item=matches[0] if len(matches)==1 else None
        result.append({'id':row.id,'inbound_date':row.inbound_date,'confirm_date':row.confirm_date,'inbound_no':row.inbound_no,'history_no':row.history_no,'part_no':row.part_no,'part_name':item.part_name if item else '', 'unit':item.unit if item else '', 'linked':bool(item),'partner_name':row.partner_name,'confirmed_qty':row.confirmed_qty,'product_type':row.product_type,'material_type':row.material_type,'present':row.present})
    return {'rows':result,'total':total,'page':page}
