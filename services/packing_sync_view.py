import json
from decimal import Decimal
from services.packing_inventory_service import packing_stock_snapshot


def packing_record_view(record, connections):
    if connections.packing_stock_cache is None:
        connections.packing_stock_cache = packing_stock_snapshot(connections.db, connections)
    stock_info = connections.packing_stock_cache[1][record.id]
    item, connection_type = connections.resolve(record.part_no, '포장')
    raw = json.loads(record.raw_json)
    return {'packing_box_id': None, 'record_source': 'EXTERNAL', 'source_record_id': record.id,
            'part_no': item.part_no if item else record.part_no,
            'part_name': item.part_name if item else raw.get('PRODUCT_NM', ''),
            'source_part_no': record.part_no, 'linked': bool(item), 'connection_type': connection_type,
            'lot_no': record.lot_no, 'packing_date': record.packing_date,
            'packing_qty': float(Decimal(record.packing_qty)),
            'shipment_qty': float(Decimal(record.shipment_qty)),
            'shipment_date': record.shipment_date, 'customer_name': record.customer_name, **stock_info}
