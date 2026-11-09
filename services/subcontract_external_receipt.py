"""Connect legacy dispatches to manual receipts, without issuing local stock again."""
import json
import re
from datetime import datetime
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from models.models import ItemMasterModel,ItemBomModel,ProcessModel,StorageLocationModel
from models.partner import Partner
from models.subcontract import SubcontractOrderMaster,SubcontractOrderItem
from models.subcontract_outbound import SubcontractOutboundMaster,SubcontractOutboundItem,SubcontractOutboundLot
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record,ExternalSubcontractReceiptSource as Link

PROCESS_NAMES={'PROC_SILVER':'은도금','PROC_TAB':'탭핑'}


def compact(value):
    return re.sub(r'\s+','',value or '').replace('(주)','').replace('㈜','').casefold()


def pending(row):
    raw=json.loads(row.raw_json)
    return row.source_status=='REQ_OUT' and Decimal(raw.get('ETC_IN_QTY') or '0')==0


def snapshot(rows):
    fields=('source_key','group_key','outbound_no','outbound_date','partner_name','process_code','part_no','previous_part_no','lot_no','outbound_qty')
    values=[{k:getattr(r,k) for k in fields} for r in sorted(rows,key=lambda r:r.source_key)]
    for value in values:value['outbound_qty']=format(Decimal(value['outbound_qty']).normalize(),'f')
    return json.dumps(values,ensure_ascii=False,sort_keys=True)


def source_rows(db,group_key):
    return db.query(Record).filter_by(group_key=group_key).order_by(Record.id).populate_existing().all()


def validate_link(db,link):
    rows=source_rows(db,link.group_key)
    if not rows or not all(pending(r) for r in rows):
        raise HTTPException(409,'외부 원본이 입고·완료 상태로 변경되었습니다. 중복 입고 방지를 위해 원출고를 확인하세요.')
    if snapshot(rows)!=link.snapshot_json:
        raise HTTPException(409,'선택 후 외부 출고의 품번·LOT·수량 등이 변경되었습니다. 원출고와 기존 입고 내역을 확인하세요.')
    return rows


def lock_source(db,outbound,validate=True):
    link=db.query(Link).filter_by(outbound_id=outbound.id).one_or_none()
    if link is None:return None
    # Serialize receipts from the same imported document before reading remaining quantities.
    db.execute(update(Link).where(Link.group_key==link.group_key).values(snapshot_json=link.snapshot_json))
    if validate:validate_link(db,link)
    return link


def _one(rows,message):
    if len(rows)!=1:raise HTTPException(422,message)
    return rows[0]


def resolve(db,rows):
    first=rows[0]
    partners=db.query(Partner).filter(Partner.is_active=='Y',Partner.partner_type.in_(('VENDOR','BOTH'))).all()
    exact=[p for p in partners if p.partner_name==first.partner_name]
    partner=_one(exact or [p for p in partners if compact(p.partner_name)==compact(first.partner_name)],f'{first.partner_name}: 내부의 활성 외주 발주처를 하나로 연결할 수 없습니다. 거래처 등록을 확인하세요.')
    name=PROCESS_NAMES.get(first.process_code)
    processes=db.query(ProcessModel).filter_by(is_active='Y').all()
    process=_one([p for p in processes if compact(p.process_name)==compact(name)],f'{name or first.process_code}: 내부 활성 가공유형을 하나로 연결할 수 없습니다.')
    locations=db.query(StorageLocationModel).filter_by(is_active='Y').all()
    location=_one([s for s in locations if compact(s.location_name)==compact('외주가공 저장위치')], '활성 외주가공 저장위치를 등록해 주세요.')
    mapped=[]
    for row in rows:
        previous=_one(db.query(ItemMasterModel).filter(func.lower(ItemMasterModel.part_no)==row.previous_part_no.casefold()).all(),f'{row.previous_part_no}: 내부 품목마스터를 확인하세요.')
        boms=db.query(ItemBomModel).filter_by(child_item_id=previous.id).all()
        matched=[b for b in boms if b.process_code==process.process_code]
        candidates={b.parent_item_id for b in (matched or boms) if b.parent_item_id and b.parent_item_id!=previous.id}
        if len(candidates)>1:
            exact=db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(candidates),func.lower(ItemMasterModel.part_no)==row.part_no.casefold()).all()
            candidates={p.id for p in exact} if len(exact)==1 else candidates
        if len(candidates)!=1:raise HTTPException(422,f'{row.previous_part_no}: {process.process_name} 후 입고할 품번을 BOM에서 하나로 연결할 수 없습니다. BOM을 확인하세요.')
        target=db.get(ItemMasterModel,next(iter(candidates)))
        if target is None or not target.unit:raise HTTPException(422,f'{row.previous_part_no}: 입고 품목 또는 단위 등록을 확인하세요.')
        mapped.append((row,previous,target))
    return partner,process,location,mapped


def prepare(db,record_id,username):
    row=db.get(Record,record_id)
    if row is None:raise HTTPException(404,'외부 외주 출고 내역을 찾을 수 없습니다.')
    link=db.get(Link,row.group_key)
    if link:
        validate_link(db,link)
        return db.get(SubcontractOutboundMaster,link.outbound_id)
    rows=source_rows(db,row.group_key)
    if not all(pending(r) for r in rows):raise HTTPException(409,'외부에서 아직 입고되지 않은 출고 상태의 전표만 수동 입고할 수 있습니다.')
    if len({r.outbound_date for r in rows})!=1:raise HTTPException(409,'같은 외부 출고 전표의 출고일이 서로 다릅니다.')
    link=Link(group_key=row.group_key,snapshot_json=snapshot(rows),created_at=datetime.now())
    try:
        db.add(link);db.flush()
    except IntegrityError:
        db.rollback()
        existing=db.get(Link,row.group_key)
        if existing:
            validate_link(db,existing)
            return db.get(SubcontractOutboundMaster,existing.outbound_id)
        raise
    rows=validate_link(db,link)
    partner,process,location,mapped=resolve(db,rows)
    if len({r.lot_no for r in rows})!=len(rows):raise HTTPException(409,'외부 출고 전표에 동일 LOT가 중복되어 있습니다.')
    lots={r.lot_no for r in rows}
    from models.production_lot import ProductionLotModel
    from models.production_sync import ExternalProductionRecord
    from models.packing_sync import ExternalPackingRecord
    from models.models import PurchaseInboundItem
    for model,column in ((ProductionLotModel,ProductionLotModel.lot_no),(ExternalProductionRecord,ExternalProductionRecord.lot_no),(ExternalPackingRecord,ExternalPackingRecord.lot_no),(PurchaseInboundItem,PurchaseInboundItem.internal_lot_no)):
        if db.query(column).filter(column.in_(lots)).first():
            raise HTTPException(409,'외부 출고 LOT와 동일한 내부 재고 또는 생산·입고 이력이 있습니다. 중복 입고 여부를 확인하세요.')
    if db.query(SubcontractOutboundLot.id).filter(SubcontractOutboundLot.lot_no.in_(lots)).first():
        raise HTTPException(409,'동일한 LOT의 내부 출고 이력이 이미 있습니다. 중복 등록 여부를 확인하세요.')
    if len(row.outbound_no)>20 or db.query(SubcontractOutboundMaster.id).filter_by(outbound_no=row.outbound_no).first():
        raise HTTPException(409,'외부 출고번호가 내부 출고번호와 중복되거나 등록 길이를 초과합니다.')
    # An order/outbound snapshot is required by existing receipt FKs. No LOT allocation,
    # production LOT, consumption or receipt is created here.
    note=f'외부 출고 연결 / 원전표 {row.outbound_no} / 출고 이전 생산 LOT 연결은 외부자료 범위 밖입니다.'
    order=SubcontractOrderMaster(order_no='EX-'+row.outbound_no,order_date=row.outbound_date,partner_id=partner.id,partner_name=partner.partner_name,processing_type_code=process.process_code,processing_type_name=process.process_name,external_storage_location=location.location_code,status='ORDERED',note=note,created_by=username)
    db.add(order);db.flush()
    outbound=SubcontractOutboundMaster(outbound_no=row.outbound_no,outbound_date=row.outbound_date,order_id=order.id,order_no=order.order_no,partner_id=partner.id,partner_name=partner.partner_name,processing_type_code=process.process_code,processing_type_name=process.process_name,external_storage_location=location.location_code,status='OUTBOUND',note=note,created_by=username)
    groups={}
    for record,previous,target in mapped:groups.setdefault((previous.id,target.id),[]).append((record,previous,target))
    for grouped in groups.values():
        _,previous,target=grouped[0];qty=sum(Decimal(r.outbound_qty) for r,_,_ in grouped)
        item=SubcontractOrderItem(previous_item_id=previous.id,item_id=target.id,previous_part_no=previous.part_no,order_part_no=target.part_no,order_part_name=target.part_name,spec=target.spec,unit=target.unit,processing_type_code=process.process_code,processing_type_name=process.process_name,order_qty=float(qty))
        order.items.append(item);db.flush()
        out_item=SubcontractOutboundItem(order_item_id=item.id,previous_item_id=previous.id,item_id=target.id,previous_part_no=previous.part_no,order_part_no=target.part_no,order_part_name=target.part_name,spec=target.spec,unit=target.unit,outbound_qty=float(qty),lot_count=len(grouped))
        out_item.lots=[SubcontractOutboundLot(lot_no=r.lot_no,outbound_qty=float(Decimal(r.outbound_qty))) for r,_,_ in sorted(grouped,key=lambda g:g[0].lot_no)]
        outbound.items.append(out_item)
    db.add(outbound);db.flush();link.outbound_id=outbound.id
    db.commit()
    return outbound


def unlinked_sources(db,keyword=None):
    linked={r[0] for r in db.query(Link.group_key)}
    groups={}
    for r in db.query(Record).order_by(Record.outbound_date.desc(),Record.id.desc()):
        if r.group_key not in linked:groups.setdefault(r.group_key,[]).append(r)
    result=[]
    for rows in groups.values():
        first=rows[0]
        if not all(pending(r) for r in rows):continue
        if keyword and compact(keyword) not in compact(' '.join([first.outbound_no,first.partner_name]+[r.part_no+' '+r.previous_part_no+' '+r.lot_no for r in rows])):continue
        qty=sum(Decimal(r.outbound_qty) for r in rows)
        result.append(dict(outbound_id=-first.id,outbound_no=first.outbound_no,outbound_date=first.outbound_date,order_no=first.outbound_no,partner_name=first.partner_name,processing_type_name=first.process_name,item_count=len({r.part_no for r in rows}),total_qty=float(qty),received_qty=0,remaining_qty=float(qty),inbound_status='WAITING',inbound_status_name='외부 · 입고대기',source='EXTERNAL'))
    return result


def validate_external_fifo(db,outbound,selected):
    """Unselected legacy dispatches must not disappear from the receipt FIFO queue."""
    selected_parts={item.previous_part_no.casefold() for item in outbound.items for lot in item.lots if lot.id in selected}
    process_name=compact(outbound.processing_type_name)
    linked={r[0] for r in db.query(Link.group_key)}
    for r in db.query(Record).filter(Record.outbound_date<=outbound.outbound_date).order_by(Record.outbound_date,Record.outbound_no,Record.lot_no):
        if r.group_key in linked or not pending(r):continue
        if compact(r.partner_name)!=compact(outbound.partner_name) or compact(PROCESS_NAMES.get(r.process_code))!=process_name or r.previous_part_no.casefold() not in selected_parts:continue
        if (r.outbound_date,r.outbound_no)<(outbound.outbound_date,outbound.outbound_no):
            raise HTTPException(409,f'외주 입고는 먼저 출고한 미입고 LOT부터 처리해야 합니다. 외부 출고 {r.outbound_no} / LOT {r.lot_no}부터 입고하세요.')
