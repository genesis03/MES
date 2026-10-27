"""Adapt external shipment snapshots to existing inquiry and label shapes."""
import json
from types import SimpleNamespace
from fastapi import HTTPException
from models.shipment_sync import ExternalShipment
from models.partner import Partner
from services.production_sync_mapping import ItemConnections
from services.shipping_analysis_adapter import _analysis_row


def resolve_item(connections, part):
    return connections.resolve(part, '출고')[0]


def shipment_view(record, connections, detail=False):
    data = json.loads(record.raw_json)
    items = []
    for row in data['items']:
        item = resolve_item(connections, row['ITEM_NUM'])
        lots = row['lots']
        values = dict(id=row['RULE_NO'], item_id=item.id if item else None,
            part_no=item.part_no if item else row['ITEM_NUM'], source_part_no=row['ITEM_NUM'],
            part_name=(item.part_name or '') if item else '', source_part_name=row.get('PRODUCT_NM', ''),
            linked=item is not None,
            shipped_qty=float(row['TR_QTY']), box_count=len(lots), order_no=row['ORDER_NUM'],
            lot_complete=row['lot_complete'])
        if detail:
            values['boxes'] = [dict(waiting_lot_no=l['LOT_NUM'], lot_qty=float(l['LOT_QTY']),
                                    shipped_qty=float(l['JOB_QTY'])) for l in lots]
        items.append(values)
    parts = list(dict.fromkeys(i['part_no'] for i in items))
    orders = list(dict.fromkeys(i['order_no'] for i in items if i['order_no']))
    complete = all(i['lot_complete'] for i in items)
    return dict(id=-record.id, source='EXTERNAL', readonly=True, shipment_no=record.shipment_no,
        shipment_date=record.shipment_date, customer_name=record.customer_name, status=record.status,
        present=record.present, order_no=', '.join(orders), items=items, part_nos=parts,
        part_summary=parts[0] + (f' 외 {len(parts)-1}건' if len(parts)>1 else '') if parts else '',
        total_qty=sum(i['shipped_qty'] for i in items), total_boxes=sum(i['box_count'] for i in items),
        box_count=sum(i['box_count'] for i in items), item_count=len(items), shipment_mode='EXTERNAL',
        printable=record.present and record.status=='OUT_FIN' and complete,
        note='외부 출고이력' + (' · LOT 확인 필요' if not complete else '') +
             (' · 품번 미연결' if any(not item['linked'] for item in items) else '') +
             (' · 원본 목록에서 제외됨' if not record.present else '') +
             (' · '+record.status if record.status!='OUT_FIN' else ''))


def external_shipments(db, start_date=None, end_date=None, shipment_no=None, customer_name=None,
                       customer_id=None, item_ids=None, limit=1000, printable_only=False):
    query = db.query(ExternalShipment).filter(ExternalShipment.present.is_(True))
    if start_date:
        query = query.filter(ExternalShipment.shipment_date >= start_date)
    if end_date:
        query = query.filter(ExternalShipment.shipment_date <= end_date)
    if shipment_no and shipment_no.strip():
        query = query.filter(ExternalShipment.shipment_no.contains(shipment_no.strip(), autoescape=True))
    if customer_id:
        partner = db.get(Partner, customer_id)
        if partner is None:
            return []
        query = query.filter(ExternalShipment.customer_name == partner.partner_name)
    elif customer_name and customer_name.strip():
        query = query.filter(ExternalShipment.customer_name.contains(customer_name.strip(), autoescape=True))
    connections = ItemConnections(db)
    result = []
    for record in query.order_by(ExternalShipment.shipment_date.desc(), ExternalShipment.id.desc()):
        row = shipment_view(record, connections)
        if item_ids and not any(i['item_id'] in item_ids for i in row['items']):
            continue
        if printable_only and not row['printable']:
            continue
        result.append(row)
        if len(result) >= limit:
            break
    return result


def external_analysis_rows(db, ids):
    connections = ItemConnections(db)
    result = []
    for record_id in dict.fromkeys(ids):
        record = db.get(ExternalShipment, record_id)
        if record is None:
            raise HTTPException(404, '외부 출고 내역을 찾을 수 없습니다.')
        view = shipment_view(record, connections, detail=True)
        if not view['printable']:
            raise HTTPException(409, '완료 상태와 LOT 배정수량이 확인된 외부 출고만 출력할 수 있습니다.')
        shipment = SimpleNamespace(customer_name=record.customer_name, shipment_date=record.shipment_date)
        for item in view['items']:
            for lot in item['boxes']:
                result.append(_analysis_row(shipment, item['part_no'], item['part_name'],
                    lot['waiting_lot_no'], lot['shipped_qty']))
    return result
