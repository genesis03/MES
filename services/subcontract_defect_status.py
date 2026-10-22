"""Read-only receipt defect history, including consumed LOTs and old allocations."""
from collections import defaultdict

from models.models import CommonCodeModel, ItemMasterModel
from models.quality import QualityInboundDefectDetail, QualityInboundLotDefect, QualityInboundResult
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from services.defect_report_format import report_types, report_quantities


def subcontract_defect_status(db, start_date=None, end_date=None, part_no='', partner_name='', limit=5000):
    query = (db.query(SubcontractInboundMaster, SubcontractInboundItem, QualityInboundResult, ItemMasterModel)
        .join(SubcontractInboundItem, SubcontractInboundItem.inbound_id == SubcontractInboundMaster.id)
        .join(QualityInboundResult, (QualityInboundResult.inbound_item_id == SubcontractInboundItem.id)
              & (QualityInboundResult.inbound_id == SubcontractInboundMaster.id)
              & (QualityInboundResult.source_type == 'SUBCONTRACT'))
        .outerjoin(ItemMasterModel, ItemMasterModel.id == SubcontractInboundItem.item_id)
        .filter(SubcontractInboundMaster.status == 'RECEIVED',
                QualityInboundResult.inspection_status == 'COMPLETED', QualityInboundResult.defect_qty > 0))
    if start_date:
        query = query.filter(SubcontractInboundMaster.inbound_date >= start_date)
    if end_date:
        query = query.filter(SubcontractInboundMaster.inbound_date <= end_date)
    if partner_name:
        query = query.filter(SubcontractInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    records = query.order_by(SubcontractInboundMaster.inbound_date.desc(), SubcontractInboundMaster.id.desc(),
                             SubcontractInboundItem.id).all()
    records = [(m, i, r, p) for m, i, r, p in records
               if not part_no or part_no.strip().casefold() in (p.part_no if p else i.part_no).casefold()]
    result_ids = [r.id for _, _, r, _ in records]
    item_ids = [i.id for _, i, _, _ in records]
    names = {c.code: c.code_name for c in db.query(CommonCodeModel)
             .filter(CommonCodeModel.group_code == 'DEFECT_TYPE').order_by(CommonCodeModel.sort_order, CommonCodeModel.id)}
    details, allocations, lots = defaultdict(list), defaultdict(list), defaultdict(list)
    if result_ids:
        for detail in db.query(QualityInboundDefectDetail).filter(QualityInboundDefectDetail.result_id.in_(result_ids)):
            details[detail.result_id].append(detail)
        for allocation in db.query(QualityInboundLotDefect).filter(QualityInboundLotDefect.result_id.in_(result_ids)):
            allocations[allocation.result_id].append(allocation)
        for lot in db.query(SubcontractInboundLot).filter(SubcontractInboundLot.inbound_item_id.in_(item_ids)).order_by(SubcontractInboundLot.id):
            lots[lot.inbound_item_id].append(lot)

    rows = []
    totals = {'inbound_qty': 0., 'good_qty': 0., 'defect_qty': 0., 'by_type': defaultdict(float)}
    for master, item, result, product in records:
        by_type = defaultdict(float)
        for detail in details[result.id]:
            by_type[detail.defect_type_code] += float(detail.defect_qty or 0)
        if not by_type and result.defect_type_code:
            by_type[result.defect_type_code] = float(result.defect_qty)
        residual = float(result.defect_qty) - sum(by_type.values())
        if residual > 1e-9:
            by_type['__UNCLASSIFIED__'] += residual
        names['__UNCLASSIFIED__'] = '미분류'
        by_type = {code: qty for code, qty in by_type.items() if qty > 0}
        qty = float(item.good_qty or 0)  # This legacy column stores total received quantity.
        bad = sum(by_type.values())
        totals['inbound_qty'] += qty
        totals['good_qty'] += max(qty - bad, 0)
        totals['defect_qty'] += bad
        for code, value in by_type.items():
            totals['by_type'][code] += value
        common = {'date': master.inbound_date, 'inbound_no': master.inbound_no,
                  'inbound_item_id': item.id, 'part_no': product.part_no if product else item.part_no,
                  'part_name': product.part_name if product else item.part_name,
                  'process_name': master.processing_type_name, 'partner_name': master.partner_name}
        lot_quantities = defaultdict(float)
        for lot in lots[item.id]:
            lot_no = (lot.child_lot_no or lot.source_lot_no or '').strip()
            if lot_no:
                lot_quantities[lot_no] += float(lot.good_qty or 0)
        allocated = {lot: defaultdict(float) for lot in lot_quantities}
        remaining = dict(by_type)
        for detail in allocations[result.id]:
            if detail.lot_no not in allocated:
                continue
            code = detail.defect_type_code
            value = min(max(float(detail.defect_qty or 0), 0), remaining.get(code, 0))
            allocated[detail.lot_no][code] += value
            remaining[code] = remaining.get(code, 0) - value
        if len(lot_quantities) == 1:
            # A single receipt LOT is unambiguous, even in old item-level results.
            allocated[next(iter(lot_quantities))] = dict(by_type)
            remaining = {}
        remaining = {code: value for code, value in remaining.items() if value > 1e-9}
        for lot_no, received in lot_quantities.items():
            values = dict(allocated[lot_no]); lot_bad = sum(values.values())
            rows.append(common | {'lot_no': lot_no, 'unallocated': False,
                'inbound_qty': received, 'good_qty': None if remaining else max(received - lot_bad, 0),
                'defect_qty': lot_bad, 'by_type': values})
        unlinked_qty = max(qty - sum(lot_quantities.values()), 0)
        if remaining or not lot_quantities or unlinked_qty > 1e-9:
            # Quantity belongs to the receipt item; do not invent a per-LOT allocation.
            rows.append(common | {'lot_no': '', 'unallocated': True,
                'inbound_qty': unlinked_qty if unlinked_qty or not lot_quantities else None,
                'good_qty': max(qty - bad, 0) if not lot_quantities else None,
                'defect_qty': sum(remaining.values()), 'by_type': remaining})
    columns = report_types(db)
    def normalize(values):
        return report_quantities([{'code': code, 'name': names.get(code, code), 'qty': qty}
                                  for code, qty in values.items()], columns)
    for row in rows:
        row['by_type'] = normalize(row['by_type'])
    totals['by_type'] = normalize(totals['by_type'])
    from services.subcontract_fault_view import merge_report
    return merge_report(db, rows, totals, len(records), columns, start_date, end_date, part_no, partner_name, limit)
