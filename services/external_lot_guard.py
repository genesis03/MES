"""Reserve received external identities when native code generates a LOT."""
from models.packing_sync import ExternalPackingRecord
from models.production_sync import ExternalProductionRecord
from services.production_sync_mapping import ItemConnections
from sqlalchemy import func


def external_lot_numbers(db, prefix, item_id=None):
    packing = db.query(ExternalPackingRecord.part_no, ExternalPackingRecord.lot_no).filter(
        func.upper(func.trim(ExternalPackingRecord.lot_no)).like(prefix.upper() + '%')).all()
    production = db.query(ExternalProductionRecord.part_no, ExternalProductionRecord.process_name,
                          ExternalProductionRecord.lot_no).filter(
        func.upper(func.trim(ExternalProductionRecord.lot_no)).like(prefix.upper() + '%')).all()
    candidates = [(part, '포장', lot) for part, lot in packing] + list(production)
    if item_id is None:
        return {lot.strip().upper() for _, _, lot in candidates}
    connections = ItemConnections(db)
    result = set()
    for part, process, lot in candidates:
        item, _ = connections.resolve(part, process)
        # Unlinked source items may later map to this item. Reserve conservatively
        # until that mapping is known rather than creating a future collision.
        if item is None or item.id == item_id:
            result.add(lot.strip().upper())
    return result
