"""Shared display values for external records and the production inquiry."""
import json
from datetime import timedelta, timezone
from decimal import Decimal
from services.packing_inventory_service import native_lot_identities, identity

def external_record_view(record, connections):
    item, connection_type = connections.resolve(record.part_no, record.process_name)
    code = connections.process_code(record.process_name)
    raw = json.loads(record.raw_json)
    setup = raw.get('F10', '')
    notes = []
    if connections.native_lot_cache is None:
        connections.native_lot_cache = native_lot_identities(connections.db)
    if item and identity(item.id, record.lot_no) in connections.native_lot_cache:
        notes.append('MES에서 이미 사용한 품목·LOT: 외부 생산 재고 미반영')
    if not item:
        notes.append('품번 연결 확인')
    if code not in connections.processes:
        notes.append('공정 연결 확인')
    total = None
    if setup == '':
        notes.append('셋업 수량 누락')
    else:
        total = Decimal(record.job_qty) + Decimal(record.fault_qty) + Decimal(setup)
        if Decimal(record.lot_qty) != total:
            notes.append('전체수량과 양품·불량·셋업 합계 확인')
    mapping = connections.process_maps.get(record.process_name)
    performance_type = mapping.performance_type if mapping else ''
    if not performance_type:
        suffix = connections.suffix(record.process_name)
        performance_type = 'ASSEMBLY' if suffix == '-C' else 'MACHINING' if suffix in ('-A', '-B', '-D') else ''
    machine = record.lot_no.strip()[-2:-1]
    return {
        'id': record.id, 'work_date': record.work_date, 'part_no': record.part_no,
        'part_name': item.part_name if item else raw.get('PRODUCT_NM', ''),
        'item_id': item.id if item else None, 'mes_part_no': item.part_no if item else '',
        'connection_type': connection_type, 'source_process': record.process_name,
        'process_code': code, 'process_name': connections.processes.get(code, ''),
        'performance_type': performance_type, 'job_no': record.job_no, 'lot_no': record.lot_no,
        'machine_no': machine if machine.isascii() and machine.isdigit() else '',
        'started_at': record.started_at or '', 'ended_at': record.ended_at or '',
        'job_qty': record.job_qty, 'lot_qty': record.lot_qty, 'fault_qty': record.fault_qty,
        'good_qty': record.job_qty, 'setup_qty': setup, 'total_qty': str(total) if total is not None else None,
        'notes': notes, 'changed_at': record.changed_at.replace(tzinfo=timezone.utc).astimezone(
            timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S'),
    }
