"""Read-only projections into the native outbound history; no native transaction IDs."""
from decimal import Decimal
from fastapi import HTTPException
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record
from models.models import ItemMasterModel


def history(db,outbound_no=None,order_no=None,partner_name=None,start_date=None,end_date=None,limit=300):
    query=db.query(Record)
    for column,value in ((Record.outbound_no,outbound_no),(Record.outbound_no,order_no),(Record.partner_name,partner_name)):
        if value:query=query.filter(column.contains(value.strip(),autoescape=True))
    if start_date:query=query.filter(Record.outbound_date>=start_date)
    if end_date:query=query.filter(Record.outbound_date<=end_date)
    groups={}
    for row in query.order_by(Record.outbound_date.desc(),Record.id.desc()):
        group=groups.setdefault(row.group_key,[]);group.append(row)
    result=[]
    for rows in groups.values():
        first=rows[0];statuses=sorted({r.source_status_name for r in rows})
        result.append(dict(outbound_id=-first.id,outbound_no=first.outbound_no,outbound_date=max(r.outbound_date for r in rows),order_id=None,order_no=first.outbound_no,partner_name=first.partner_name,processing_type_name=first.process_name,item_count=len({(r.part_no,r.previous_part_no) for r in rows}),status='EXTERNAL',status_name='외부 · '+' / '.join(statuses),source='EXTERNAL'))
    return sorted(result,key=lambda r:(r['outbound_date'],r['outbound_id']),reverse=True)[:limit]


def detail(db,record_id):
    first=db.get(Record,record_id)
    if first is None:raise HTTPException(404,'외부 외주 출고 내역을 찾을 수 없습니다.')
    rows=db.query(Record).filter_by(group_key=first.group_key).order_by(Record.id).all()
    masters={}
    for item in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_({r.part_no for r in rows})):
        masters.setdefault(item.part_no.casefold(),[]).append(item)
    groups={}
    for row in rows:groups.setdefault((row.part_no,row.previous_part_no),[]).append(row)
    items=[]
    for (part,previous),lots in groups.items():
        matches=masters.get(part.casefold(),[]);master=matches[0] if len(matches)==1 else None
        items.append(dict(order_part_no=part,order_part_name=master.part_name if master else '',previous_part_no=previous,spec=master.spec if master else '',unit=master.unit if master else '',outbound_qty=str(sum(Decimal(r.outbound_qty) for r in lots)),lot_count=len(lots),lots=[dict(lot_no=r.lot_no,outbound_qty=r.outbound_qty) for r in lots],note=' / '.join(f'{r.lot_no}: {r.source_status_name}' for r in lots)))
    return dict(id=-first.id,source='EXTERNAL',status='EXTERNAL',status_name='외부 · '+' / '.join(sorted({r.source_status_name for r in rows})),outbound_no=first.outbound_no,outbound_date=first.outbound_date,order_no=first.outbound_no,order_date=first.outbound_date,partner_name=first.partner_name,processing_type_name=first.process_name,external_storage_location_name=first.storage_name,manager_name='',items=items)
