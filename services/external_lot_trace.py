"""External packing/shipping evidence; never infer missing production ancestry."""
from decimal import Decimal
from models.packing_sync import ExternalPackingRecord, PackingSourcePresence
from models.shipment_sync import ExternalShipment, ExternalShipmentLot
from services.production_sync_mapping import ItemConnections


def external_outbound_lots(db,keyword,part_no='',exact=False):
    connections=ItemConnections(db)
    missing={r.record_id for r in db.query(PackingSourcePresence).filter(PackingSourcePresence.present.is_(False))}
    groups={}
    def add(raw_part,process,lot,qty,day,reference,kind):
        item,_=connections.resolve(raw_part,process)
        part=item.part_no if item else raw_part
        if part_no and part_no.casefold() not in part.casefold():return
        key=(lot,part)
        record=groups.setdefault(key,{'search_type':'OUTBOUND','source':'EXTERNAL','lot_no':lot,'part_no':part,'qty':0.,'date':'','reference':'','evidence':[]})
        record['evidence'].append({'process':kind,'lot_date':day,'qty':float(Decimal(str(qty))),'reference':reference})
    query=db.query(ExternalPackingRecord)
    query=query.filter(ExternalPackingRecord.lot_no==keyword) if exact else query.filter(ExternalPackingRecord.lot_no.contains(keyword,autoescape=True))
    for row in query:
        if row.id not in missing:add(row.part_no,'포장',row.lot_no,row.packing_qty,row.packing_date,'외부 포장','외부 포장')
    query=(db.query(ExternalShipmentLot,ExternalShipment).join(ExternalShipment,ExternalShipment.id==ExternalShipmentLot.shipment_id)
           .filter(ExternalShipment.present.is_(True),ExternalShipment.status=='OUT_FIN'))
    query=query.filter(ExternalShipmentLot.lot_no==keyword) if exact else query.filter(ExternalShipmentLot.lot_no.contains(keyword,autoescape=True))
    for lot,master in query:add(lot.part_no,'출고',lot.lot_no,lot.allocated_qty,master.shipment_date,master.shipment_no,'외부 출고')
    for row in groups.values():
        shipped=[e for e in row['evidence'] if e['process']=='외부 출고']
        chosen=shipped or row['evidence']
        row['qty']=sum(e['qty'] for e in chosen)
        row['date']=max(e['lot_date'] for e in chosen)
        row['reference']=' / '.join(dict.fromkeys(e['reference'] for e in chosen))
    return list(groups.values())


def external_trace(db,lot_no,part_no):
    groups=external_outbound_lots(db,lot_no,part_no,exact=True)
    if part_no:groups=[row for row in groups if row['part_no']==part_no]
    rows=[]
    for group in groups:
        for e in group['evidence']:
            rows.append(dict(process=e['process'],lot_no=lot_no,lot_date=e['lot_date'],part_no=group['part_no'],
                             external_lot_no='',child_lot_no='',child_lot_qty=None,child_part_no='',child_external_lot_no='',
                             consumed_qty=None,tree=e['reference']))
    return {'root_lot_no':lot_no,'rows':rows,'lot_count':1 if rows else 0,'row_count':len(rows),
            'notes':['외부 연동에서 확인된 포장·출고 내역입니다. 원생산 LOT 연결 자료는 없어 공정 계보를 추정하지 않습니다.'] if rows else []}
