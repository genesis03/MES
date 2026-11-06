"""Read-only external receipts for the ordinary purchase inquiry page."""
from datetime import date
from fastapi import HTTPException
from models.inbound_sync import ExternalInboundRecord
from models.models import ItemMasterModel


def external_inbound_inquiry(db,start_date=None,end_date=None,inbound_no=None,po_no=None,partner_name=None,part_no=None,lot=None,status=None,offset=0,limit=500):
    for value in (start_date,end_date):
        if value:
            try:date.fromisoformat(value)
            except ValueError as exc:raise HTTPException(422,'조회 날짜를 확인하세요.') from exc
    if start_date and end_date and start_date>end_date:raise HTTPException(422,'시작일은 종료일 이후일 수 없습니다.')
    if status not in (None,'','DRAFT','CONFIRMED'):raise HTTPException(422,'지원하지 않는 입고 상태입니다.')
    # The observed receipt response has no verified PO or LOT field. Never invent one.
    if status=='DRAFT' or (po_no or '').strip() or (lot or '').strip():return {'total':0,'offset':offset,'limit':limit,'items':[]}
    query=db.query(ExternalInboundRecord).filter(ExternalInboundRecord.present.is_(True))
    if start_date:query=query.filter(ExternalInboundRecord.inbound_date>=start_date)
    if end_date:query=query.filter(ExternalInboundRecord.inbound_date<=end_date)
    for field,value in [('inbound_no',inbound_no),('partner_name',partner_name),('part_no',part_no)]:
        if value and value.strip():query=query.filter(getattr(ExternalInboundRecord,field).contains(value.strip(),autoescape=True))
    total=query.count()
    records=query.order_by(ExternalInboundRecord.inbound_date.desc(),ExternalInboundRecord.id.desc()).offset(offset).limit(limit).all()
    products={}
    for item in db.query(ItemMasterModel):products.setdefault(item.part_no.casefold(),[]).append(item)
    rows=[]
    for record in records:
        matches=products.get(record.part_no.casefold(),[]);item=matches[0] if len(matches)==1 else None
        rows.append({'type':'GENERAL','source_type':'EXTERNAL_INBOUND','source':'EXTERNAL','readonly':True,
            'item_id':item.id if item else None,'inbound_id':-record.id,'inbound_item_id':-record.id,
            'inbound_no':record.inbound_no,'inbound_date':record.inbound_date,'po_no':'','partner_id':None,
            'partner_name':record.partner_name,'part_no':record.part_no,'part_name':item.part_name if item else '',
            'spec':(item.spec or '') if item else '', 'inbound_qty':record.confirmed_qty,'unit':item.unit if item else '',
            'supplier_lot_no':'','internal_lot_no':'','warehouse_code':'','warehouse_name':'',
            'storage_location':'','storage_location_name':'','status':'CONFIRMED','status_name':'입고확정',
            'inspection_status':'','note':f'외부 입고현황 · 확정일 {record.confirm_date} · {record.product_type} · {record.material_type}'.rstrip(' ·')})
    return {'total':total,'offset':offset,'limit':limit,'items':rows}
