"""Production LOTs are global; packing LOTs are unique per resolved item."""
from collections import Counter

from models.models import PurchaseInboundItem
from models.production_lot import ProductionLotModel
from models.packing import PackingBox
from models.packing_sync import ExternalPackingRecord
from models.production_sync import ExternalProductionRecord
from models.shipping_lot import ShippingLotRegistry
from models.sales import ShipmentDirectLot
from services.packing_inventory_service import native_lot_identities, identity, production_lot_numbers
from services.production_sync_mapping import ItemConnections
from services.production_sync_client import SyncError


def lot_identity(value):
    return str(value or '').strip().upper()


def native_lot_numbers(db):
    values = set()
    for field in (ProductionLotModel.lot_no, PurchaseInboundItem.internal_lot_no,
                  PackingBox.package_lot_no, ShippingLotRegistry.lot_no, ShipmentDirectLot.outbound_lot_no):
        values.update(lot_identity(lot) for (lot,) in db.query(field) if lot)
    return values


def production_conflicts(db):
    native = native_lot_numbers(db)
    packed = {lot_identity(lot) for (lot,) in db.query(ExternalPackingRecord.lot_no)}
    counts = Counter(lot_identity(lot) for (lot,) in db.query(ExternalProductionRecord.lot_no))
    result = {}
    for lot, count in counts.items():
        if lot in native:
            result[lot] = '내부 LOT와 중복: 외부 생산실적 확인 필요'
        elif lot in packed:
            result[lot] = '외부 포장 LOT와 중복: 외부 생산실적 확인 필요'
        elif count > 1:
            result[lot] = '외부 생산 LOT 중복: 실적 확인 필요'
    return result


def prepare_import(db, rows, kind, record_model, key_for):
    """Return existing records by canonical identity without deleting old data."""
    if not rows:
        return {}
    if kind == 'production':
        lots = [lot_identity(row['LOT_NUM']) for row in rows]
        if len(set(lots)) != len(lots):
            raise SyncError('품번·작업번호·공정과 관계없이 생산 LOT가 중복되어 저장을 중단했습니다.')
        incoming = set(lots)
        used = native_lot_numbers(db)
        used.update(lot_identity(lot) for (lot,) in db.query(ExternalPackingRecord.lot_no))
        if incoming & used:
            raise SyncError('내부 또는 외부 포장에서 이미 사용한 생산 LOT가 있어 저장을 중단했습니다.')
        existing = {}
        for record in db.query(record_model):
            lot = lot_identity(record.lot_no)
            if lot not in incoming:
                continue
            if lot in existing:
                raise SyncError('기존 외부 생산 LOT가 중복되어 저장을 중단했습니다. 먼저 기존 내역을 확인해 주세요.')
            existing[lot] = record
        result = {}
        for row in rows:
            record = existing.get(lot_identity(row['LOT_NUM']))
            if record:
                if record.part_no.strip().casefold() != row['ITEM_NUM'].strip().casefold():
                    raise SyncError('같은 생산 LOT에 다른 외부 품번이 수신되어 기존 실적을 덮어쓰지 않았습니다.')
                result[key_for(row, kind)] = record
        return result

    connections = ItemConnections(db)
    native = native_lot_identities(db)
    production_numbers = production_lot_numbers(db)
    stored = {}
    mapped = {}
    for record in db.query(record_model):
        raw = (record.part_no.strip().casefold(), lot_identity(record.lot_no))
        stored.setdefault(raw, []).append(record)
        item, _ = connections.resolve(record.part_no, '포장')
        if item:
            mapped.setdefault(identity(item.id, record.lot_no), []).append(record)
    result, seen = {}, set()
    for row in rows:
        if lot_identity(row['LOT_NUM']) in production_numbers:
            raise SyncError('품번과 관계없이 내부·외부 생산 LOT와 겹쳐 외부 포장 저장을 중단했습니다.')
        raw = (row['ITEM_NUM'].strip().casefold(), lot_identity(row['LOT_NUM']))
        matches = stored.get(raw, [])
        if len(matches) > 1:
            raise SyncError('기존 외부 품번·포장 LOT가 중복되어 저장을 중단했습니다.')
        record = matches[0] if matches else None
        item, _ = connections.resolve(row['ITEM_NUM'], '포장')
        if item:
            pair = identity(item.id, row['LOT_NUM'])
            if pair in native:
                raise SyncError('같은 MES 품목·포장 LOT가 내부에 있어 외부 포장 저장을 중단했습니다.')
            if pair in seen or any(other.id != (record.id if record else None) for other in mapped.get(pair, [])):
                raise SyncError('같은 MES 품목·포장 LOT로 연결되는 외부 내역이 중복되어 저장을 중단했습니다.')
            seen.add(pair)
        if record:
            result[key_for(row, kind)] = record
    return result
