import json
from models.subcontract_fault_sync import ExternalSubcontractFault, SubcontractFaultFieldMap
from services.subcontract_fault_client import FAULT_FIELDS, QUANTITY_FIELDS, amount
from services.defect_report_format import report_types
from services.production_sync_mapping import ItemConnections


def field_settings(db):
    row = db.get(SubcontractFaultFieldMap, 1)
    defaults = {'defects': {'F1':'1', 'F2':'2', 'F3':'3', 'F4':'4', 'F5':'6',
                           'F6':'7', 'F7':'9', 'F8':'11', 'F9':'12', 'F11':'13'},
                'inbound_field':'ORI_LOT_QTY', 'good_field':'INBOUND_MINUS_DEFECT', 'partner_field':''}
    saved = json.loads(row.settings_json) if row else {}
    # Replace the former unconfirmed mappings once with the user-verified layout.
    return defaults | (saved if saved.get('mapping_version') == 2 else {})


def defect_rate(bad, received):
    return bad / received * 100 if bad is not None and received is not None and received > 0 else None


def record_view(record, connections, settings, columns):
    raw = json.loads(record.raw_json)
    item, link = connections.resolve(record.part_no, record.process_name)
    bad = float(amount(raw.get('REST_QTY') or record.defect_qty))
    values = {column['code']: 0. for column in columns}
    notes = []
    if bad != float(amount(record.defect_qty)):
        notes.append('원본 불량 수량과 불량 합계가 다름: 불량 계는 REST_QTY 기준')
    unmapped = []
    for field in FAULT_FIELDS:
        qty = float(amount(raw.get(field) or '0'))
        code = settings['defects'].get(field)
        if code in values:
            values[code] += qty
        elif qty:
            unmapped.append(field)
    if unmapped:
        notes.append('불량 유형 연결 필요: ' + ', '.join(unmapped))
    type_total = sum(float(amount(raw.get(f) or '0')) for f in FAULT_FIELDS)
    complete = not unmapped and abs(type_total - bad) < 1e-7
    if abs(type_total - bad) >= 1e-7:
        notes.append('원본 불량 합계와 유형별 합계 불일치')
    if not complete:
        values = {code: None for code in values}
    def quantity(field):
        value = raw.get(field, '') if field in QUANTITY_FIELDS else ''
        return float(amount(value)) if value != '' else None
    received = quantity(settings['inbound_field'])
    good = quantity(settings['good_field'])
    if settings['good_field'] == 'INBOUND_MINUS_DEFECT' and received is not None:
        good = received - bad
    if received is not None and (received < bad or good is not None and (good < 0 or good > received)):
        notes.append('연결한 입고·양품 수량 기준 확인 필요')
        received = good = None
    if not record.present:
        notes.append('정상 재조회에서 원본 누락: 현황 집계 제외')
    if not item:
        notes.append('내부 품번 미연결: 원본 품번 표시')
    partner = raw.get(settings['partner_field'], '') if settings['partner_field'] in ('APPLY_COMP_NM', 'ORDER_COMP_NM') else ''
    return {'id': record.id, 'date': record.confirm_date, 'inbound_no': record.inbound_no,
            'inbound_item_id': f'external-subcontract:{record.id}', 'part_no': item.part_no if item else record.part_no,
            'part_name': item.part_name if item else raw.get('PRODUCT_NM', ''), 'source_part_no': record.part_no,
            'item_id': item.id if item else None, 'connection_type': link,
            'process_name': record.process_name, 'partner_name': partner, 'lot_no': record.lot_no,
            'inbound_qty': received, 'good_qty': good, 'defect_qty': bad, 'by_type': values,
            'defect_rate': defect_rate(bad, received),
            'sample_qty': quantity('SAMPLE_QTY'), 'keep_qty': quantity('KIPUP_QTY'),
            'used_qty': quantity('JOB_QTY'), 'available_qty': quantity('LOT_QTY'),
            'loss_qty': quantity('LOSS_QTY'), 'supplier_lot_no': raw.get('CUS_LOT_NUM', ''),
            'record_source': 'EXTERNAL_SUBCONTRACT', 'unallocated': False, 'present': record.present,
            'types_complete': complete, 'notes': notes, 'raw': raw,
            'changed_at': record.changed_at.isoformat(timespec='seconds')}


def external_rows(db, start_date=None, end_date=None, part_no='', partner_name=''):
    query = db.query(ExternalSubcontractFault).filter(ExternalSubcontractFault.present.is_(True))
    if start_date:
        query = query.filter(ExternalSubcontractFault.confirm_date >= str(start_date))
    if end_date:
        query = query.filter(ExternalSubcontractFault.confirm_date <= str(end_date))
    settings, columns, connections = field_settings(db), report_types(db), ItemConnections(db)
    rows = []
    for record in query.order_by(ExternalSubcontractFault.confirm_date.desc(), ExternalSubcontractFault.id.desc()):
        view = record_view(record, connections, settings, columns)
        if view['defect_qty'] <= 0:
            continue
        if part_no and part_no.strip().casefold() not in view['part_no'].casefold():
            continue
        if partner_name and partner_name.strip().casefold() not in view['partner_name'].casefold():
            continue
        rows.append(view)
    return rows


def merge_report(db, rows, totals, receipt_count, columns, start_date, end_date, part_no, partner_name, limit):
    for row in rows:
        row['record_source'] = 'MES'
        row['defect_rate'] = (defect_rate(row['defect_qty'], row['inbound_qty'])
                              if row['good_qty'] is not None else None)
        row.setdefault('sample_qty', None)
        row.setdefault('keep_qty', None)
    for field in ('sample_qty', 'keep_qty'):
        values = [r[field] for r in rows]
        totals[field] = sum(values) if all(v is not None for v in values) else None
    def identity(row):
        return tuple(str(row[k] or '').strip().casefold() for k in ('date', 'inbound_no', 'part_no', 'lot_no', 'process_name'))
    native = {identity(row) for row in rows if row['lot_no'] and not row['unallocated']}
    external = [row for row in external_rows(db, start_date, end_date, part_no, partner_name) if identity(row) not in native]
    for row in external:
        for field in ('inbound_qty', 'good_qty', 'defect_qty', 'sample_qty', 'keep_qty'):
            totals[field] = None if totals[field] is None or row[field] is None else totals[field] + row[field]
        for column in columns:
            code = column['code']
            value = row['by_type'][code]
            totals['by_type'][code] = None if totals['by_type'].get(code) is None or value is None else totals['by_type'][code] + value
    # Keep each receipt's existing LOT order while merging sources by date.
    combined = sorted(rows + external, key=lambda r: r['date'], reverse=True)
    notes = []
    if any(r['inbound_qty'] is None or r['good_qty'] is None for r in external):
        notes.append('외부 입고·양품 수량이 미연결이거나 확인이 필요하여 해당 수량과 전체 합계를 —로 표시합니다.')
    if any(not r['types_complete'] for r in external):
        notes.append('외부 불량 유형 연결 또는 원본 합계 확인이 필요하여 유형별 수량과 합계를 —로 표시합니다. 불량 계는 원본 수량입니다.')
    totals['defect_rate'] = defect_rate(totals['defect_qty'], totals['inbound_qty'])
    if any(r['keep_qty'] is None for r in rows):
        notes.append('내부 입고의 킵업 수량은 별도 기록이 없어 —로 표시합니다.')
    return {'items': combined[:limit], 'total': len(combined),
            'receipt_item_count': receipt_count + len({(r['date'],r['inbound_no'],r['part_no'],r['process_name']) for r in external}),
            'truncated': len(combined) > limit, 'defect_types': columns, 'totals': totals, 'notes': notes}
