"""Resolve only complete assembly groups with exactly one defective LOT."""
import json
from decimal import Decimal, InvalidOperation
from models.production_sync import ExternalProductionRecord
from services.lot_uniqueness import lot_identity


def allocated_defects(record, raw, connections):
    details = raw.get('ASSEMBLY_GROUP_DEFECTS')
    lots = raw.get('ASSEMBLY_GROUP_LOTS')
    if not details or not lots:
        return None
    identities = tuple(sorted(lot_identity(lot) for lot in lots))
    key = (record.work_date, record.part_no, record.process_name,
           raw.get('MACHINE_NAME'), raw.get('SHIFT_TYPE'), identities)
    cache = getattr(connections, '_assembly_defect_allocations', None)
    if cache is None:
        cache = connections._assembly_defect_allocations = {}
    if key not in cache:
        resolved = None
        members = connections.db.query(ExternalProductionRecord).filter(
            ExternalProductionRecord.lot_no.in_(identities)).all()
        try:
            def signature(values):
                return sorted((str(d.get('code')), str(d.get('name')), Decimal(str(d['qty']))) for d in values)
            valid = len(members) == len(identities) == len(set(identities))
            for member in members:
                data = json.loads(member.raw_json)
                valid = valid and (
                    (member.work_date, member.part_no, member.process_name,
                     data.get('MACHINE_NAME'), data.get('SHIFT_TYPE'),
                     tuple(sorted(lot_identity(lot) for lot in data.get('ASSEMBLY_GROUP_LOTS', [])))) == key
                    and signature(data.get('ASSEMBLY_GROUP_DEFECTS', [])) == signature(details)
                    and not connections.production_conflict_cache.get(lot_identity(member.lot_no)))
            quantities = [(member, Decimal(member.fault_qty)) for member in members]
            positive = [(member, qty) for member, qty in quantities if qty > 0]
            total = sum(Decimal(str(d['qty'])) for d in details)
            if (valid and len(positive) == 1 and all(qty.is_finite() and qty >= 0 for _, qty in quantities)
                    and all(Decimal(str(d['qty'])).is_finite() and Decimal(str(d['qty'])) >= 0 for d in details)
                    and total == positive[0][1]):
                target = lot_identity(positive[0][0].lot_no)
                resolved = {lot: [dict(d) for d in details] if lot == target else [] for lot in identities}
        except (ValueError, TypeError, KeyError, InvalidOperation):
            pass
        cache[key] = resolved
    allocation = cache[key]
    return allocation.get(lot_identity(record.lot_no)) if allocation is not None else None
