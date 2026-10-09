"""Shared eligibility for read-only external LOT label printing."""
from decimal import Decimal, InvalidOperation


def label_metadata(view, kind):
    error = ''
    if not (view.get('item_id') if kind == 'production' else view.get('linked')):
        error = 'MES 품번을 연결해 주세요.'
    elif kind == 'production' and view.get('lot_conflict'):
        error = '중복 LOT는 출력할 수 없습니다.'
    elif kind == 'packing' and view.get('stock_status') != 'READY':
        error = view.get('stock_note') or '출고 완료 또는 재고 확인이 필요한 LOT입니다.'
    lot = str(view.get('lot_no') or '').strip()
    if not error and (not lot or not all(32 <= ord(c) <= 126 for c in lot)):
        error = '라벨 바코드로 표시할 수 없는 LOT입니다.'
    if not error:
        try:
            fields = ('good_qty', 'fault_qty', 'setup_qty') if kind == 'production' else ('packing_qty',)
            quantities = [Decimal(str(view.get(k, ''))) for k in fields]
            if any(not q.is_finite() or q < 0 for q in quantities) or quantities[0] <= 0:
                error = '출력 수량을 확인해 주세요.'
        except (InvalidOperation, ValueError):
            error = '출력 수량을 확인해 주세요.'
    record_id = view['id'] if kind == 'production' else view['source_record_id']
    return {'can_print_label': not bool(error), 'label_error': error,
            'label_url': f'/internal-labels/external-{kind}/{record_id}?auto=1' if not error else ''}
