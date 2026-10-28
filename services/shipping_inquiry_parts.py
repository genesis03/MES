"""Part totals over the full filtered period, with document-level actions preserved."""
from decimal import Decimal


def build_part_report(shipments, selected_item_ids, limit):
    selected = set(selected_item_ids)
    details, summary = [], {}
    documents = set()
    for shipment in shipments:
        groups = {}
        for index, item in enumerate(shipment['items']):
            if selected and item.get('item_id') not in selected:
                continue
            key = item['part_no'].casefold()
            group = groups.setdefault(key, dict(part_no=item['part_no'], part_name=item.get('part_name', ''),
                qty=Decimal(0), lots=set()))
            if not group['part_name']:
                group['part_name'] = item.get('part_name', '')
            group['qty'] += Decimal(str(item['shipped_qty']))
            for box in item.get('boxes', []):
                group['lots'].add(('LOT', box['waiting_lot_no']))
            for lot in item.get('direct_lots', []):
                if lot.get('outbound_lot_no'):
                    group['lots'].add(('LOT', lot['outbound_lot_no']))
            # Older direct shipments can lack an outbound LOT number. Keep their
            # existing box count rather than inventing a LOT identity.
            if not item.get('boxes') and not any(l.get('outbound_lot_no') for l in item.get('direct_lots', [])):
                for number in range(item.get('box_count', 0) + item.get('direct_lot_count', 0)):
                    group['lots'].add(('LEGACY', index, number))
        if groups:
            documents.add(shipment['id'])
        for key, group in sorted(groups.items()):
            row = {k: v for k, v in shipment.items() if k != 'items'}
            row.update(part_no=group['part_no'], part_name=group['part_name'],
                       total_qty=float(group['qty']), total_boxes=len(group['lots']))
            details.append(row)
            total = summary.setdefault(key, dict(part_no=group['part_no'], part_name=group['part_name'],
                qty=Decimal(0), box_count=0, shipment_count=0))
            if not total['part_name']:
                total['part_name'] = group['part_name']
            total['qty'] += group['qty']; total['box_count'] += len(group['lots']); total['shipment_count'] += 1
    totals = [dict(part_no=r['part_no'], part_name=r['part_name'], shipped_qty=float(r['qty']),
                   box_count=r['box_count'], shipment_count=r['shipment_count']) for _, r in sorted(summary.items())]
    return dict(items=details[:limit], total=len(details), summary=totals,
        totals=dict(shipped_qty=float(sum((r['qty'] for r in summary.values()), Decimal(0))),
                    box_count=sum(r['box_count'] for r in summary.values()), shipment_count=len(documents)),
        truncated=len(details)>limit)
