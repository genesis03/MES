"""Project packing balances without manufacturing a second stock ledger.

Native transactions win identity conflicts. Ambiguous external identities are
visible in packing status but cannot contribute to stock totals.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation

from models.models import PurchaseInboundItem, ItemMasterModel
from models.production_lot import ProductionLotModel
from models.packing import PackingBox, PackingMaster
from models.packing_sync import ExternalPackingRecord, PackingSourcePresence
from models.sales import ShipmentBox, ShipmentItem, ShipmentMaster, ShipmentDirectLot
from models.shipping_lot import ShippingLotRegistry
from services.production_sync_mapping import ItemConnections, is_finished


def identity(item_id, lot_no):
    return int(item_id), str(lot_no or '').strip().casefold()


def native_lot_identities(db):
    owners = {identity(item, lot) for item, lot in db.query(
        ProductionLotModel.item_id, ProductionLotModel.lot_no) if lot}
    owners.update(identity(item, lot) for item, lot in db.query(
        PurchaseInboundItem.item_id, PurchaseInboundItem.internal_lot_no) if lot and item)
    owners.update(identity(item, lot) for item, lot in db.query(
        PackingMaster.item_id, PackingBox.package_lot_no).join(
            PackingBox, PackingBox.packing_id == PackingMaster.id) if lot)
    owners.update(identity(item, lot) for item, lot in db.query(
        ShippingLotRegistry.item_id, ShippingLotRegistry.lot_no) if lot and item)
    owners.update(identity(item, lot) for item, lot in db.query(
        ShipmentItem.item_id, ShipmentDirectLot.outbound_lot_no).join(
            ShipmentDirectLot, ShipmentDirectLot.shipment_item_id == ShipmentItem.id) if lot and item)
    by_part = {part.casefold(): item for item, part in db.query(ItemMasterModel.id, ItemMasterModel.part_no)}
    for part, lot in db.query(ShippingLotRegistry.part_no, ShippingLotRegistry.lot_no).filter(
            ShippingLotRegistry.item_id.is_(None)):
        item = by_part.get(part.casefold())
        if item and lot:
            owners.add(identity(item, lot))
    return owners


def packing_stock_snapshot(db, connections=None):
    connections = connections or ItemConnections(db)
    native = native_lot_identities(db)
    records = db.query(ExternalPackingRecord).all()
    missing = {r.record_id for r in db.query(PackingSourcePresence).filter(PackingSourcePresence.present.is_(False))}
    resolved = {r.id: connections.resolve(r.part_no, '포장')[0] for r in records}
    counts = Counter(identity(resolved[r.id].id, r.lot_no) for r in records if resolved[r.id])
    rows, info = [], {}
    for record in records:
        item = resolved[record.id]
        status, note = 'READY', ''
        remaining = None
        if record.id in missing:
            status, note = 'MISSING', '정상 조회에서 원본 내역 누락: 재고 제외'
        elif item is None:
            status, note = 'UNLINKED', '품번 미연결: 재고 제외'
        elif not is_finished(item):
            status, note = 'INVALID_ITEM', '완제품 품목 연결 필요: 재고 제외'
        elif identity(item.id, record.lot_no) in native:
            status, note = 'CONFLICT', 'MES에서 이미 사용한 품목·LOT: 외부 재고 제외'
        elif counts[identity(item.id, record.lot_no)] > 1:
            status, note = 'CONFLICT', '여러 외부 품번이 같은 MES 품목·LOT로 연결됨: 재고 제외'
        else:
            try:
                packed, shipped = Decimal(record.packing_qty), Decimal(record.shipment_qty)
                if not packed.is_finite() or not shipped.is_finite() or shipped < 0 or packed < shipped:
                    raise InvalidOperation
                remaining = float(packed - shipped)
            except (InvalidOperation, ValueError):
                status, note = 'INVALID_QTY', '포장·출고 수량 확인 필요: 재고 제외'
            else:
                status = 'SHIPPED' if remaining == 0 else 'READY'
                rows.append({'source': '외부 포장', 'record_source': 'EXTERNAL',
                             'source_record_id': record.id, 'item_id': item.id,
                             'part_no': item.part_no, 'part_name': item.part_name or '',
                             'lot_no': record.lot_no, 'created_at': record.packing_date,
                             'lot_qty': float(packed), 'used_qty': float(shipped),
                             'remaining_qty': remaining, 'adjustment_qty': 0.0,
                             'storage_location': item.inbound_loc or '', 'read_only': True})
        info[record.id] = {'stock_status': status, 'stock_note': note, 'stock_qty': remaining}

    # Native packing previously consumed the input lots. Its unshipped boxes
    # belong to the finished item, so count those boxes once in the same view.
    boxes = db.query(PackingBox, PackingMaster).join(PackingMaster).filter(
        PackingMaster.status == 'PACKED').all()
    native_counts = Counter(identity(m.item_id, b.package_lot_no) for b, m in boxes)
    other_native = {identity(item, lot) for item, lot in db.query(
        ProductionLotModel.item_id, ProductionLotModel.lot_no) if lot}
    other_native.update(identity(item, lot) for item, lot in db.query(
        PurchaseInboundItem.item_id, PurchaseInboundItem.internal_lot_no) if item and lot)
    shipped_by_box = {}
    for box_id, qty in db.query(ShipmentBox.packing_box_id, ShipmentBox.shipped_qty).join(
            ShipmentItem, ShipmentItem.id == ShipmentBox.shipment_item_id).join(
            ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id).filter(
                ShipmentMaster.status == 'CONFIRMED'):
        shipped_by_box[box_id] = shipped_by_box.get(box_id, Decimal(0)) + Decimal(str(qty or 0))
    for box, master in boxes:
        key = identity(master.item_id, box.package_lot_no)
        item = connections.by_id.get(master.item_id)
        if item is None or key in other_native or native_counts[key] != 1:
            continue
        packed, shipped = Decimal(str(box.box_qty or 0)), shipped_by_box.get(box.id, Decimal(0))
        rows.append({'source': 'MES 포장', 'record_source': 'MES', 'packing_box_id': box.id,
                     'item_id': item.id, 'part_no': item.part_no, 'part_name': item.part_name or '',
                     'lot_no': box.package_lot_no, 'created_at': master.packing_date,
                     'lot_qty': float(packed), 'used_qty': float(shipped),
                     'remaining_qty': float(max(packed - shipped, Decimal(0))), 'adjustment_qty': 0.0,
                     'storage_location': item.inbound_loc or '', 'read_only': True})
    return rows, info
