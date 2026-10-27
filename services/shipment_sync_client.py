"""Read-only shipment SEARCH / Load protocol observed in UNI_MES captures."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from services.production_sync_client import ProductionClient, SyncError, client_common, query_with_retry

HEADER = 'MES.Out.Set.OutDirectSet.'
DETAIL = 'MES.Out.Set.TradeDetailSet.'
LOT = 'Common.Set.LotSet.'
COND = 'MES.Out.Set.OutDirectCondSet.'


def scalar(values, key):
    result = values.get(key, [''])
    if len(result) != 1:
        raise SyncError('출고 응답에 중복 필드가 있어 저장하지 않았습니다.')
    return result[0].strip()


def envelope(values):
    if values.get('SO.NET.END') != ['SO.NET.END'] or any(k.startswith('THROW.') for k in values):
        raise SyncError('정상적인 출고 조회 응답이 아닙니다.')
    if any(scalar(values, k) for k in ('jump.url', 'jump.object.name', 'jump.form.message', 'jump.form.code')):
        raise SyncError('출고 조회 대신 화면 전환 응답을 받았습니다. 계정 권한을 확인하세요.')


def columns(values, prefix, fields, count, optional=()):
    rows = [{} for _ in range(count)]
    for field in fields:
        raw = scalar(values, prefix + field)
        cells = raw.split(',') if raw else [''] * count
        if len(cells) != count:
            raise SyncError(
                f'출고 {field} 항목 개수 불일치: 상세 기준 {count}건, 수신 {len(cells)}개. '
                '쉼표 구분 응답이므로 값 내부의 쉼표 또는 누락 여부 확인이 필요합니다.',
                {'field': prefix + field, 'expected_count': count, 'received_count': len(cells),
                 'reason': 'column_count_mismatch'})
        missing = [i + 1 for i, value in enumerate(cells) if not value.strip()]
        if field not in optional and missing:
            raise SyncError(f'출고 {field} 필수값 누락: {len(missing)}개 행.',
                {'field': prefix + field, 'expected_count': count, 'received_count': len(cells),
                 'reason': 'missing_required_value', 'empty_rows': missing})
        for row, value in zip(rows, cells):
            row[field] = value.strip()
    declared = scalar(values, prefix + 'ROW_COUNT')
    if declared and (not declared.isdecimal() or int(declared) != count):
        raise SyncError('출고 서버 건수와 수신 건수가 다릅니다. 페이지 처리를 확인하세요.')
    return rows


def number(value):
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise SyncError('출고·LOT 수량이 숫자가 아닙니다.') from None
    if not result.is_finite() or result < 0 or result > Decimal('1e15'):
        raise SyncError('출고·LOT 수량의 범위를 확인하세요.')
    return result


def parse_list(values, start, end):
    envelope(values)
    raw = scalar(values, HEADER + 'RULE_NO')
    count = len(raw.split(',')) if raw else 0
    if not count:
        completed = scalar(values, 'log.invoke.object').startswith('Common.Pop.OutDirect_') and all(
            scalar(values, k) for k in ('MONITOR.QUERY.START', 'MONITOR.QUERY.END'))
        if not completed or any(scalar(values, HEADER + k) for k in ('OUT_NUM', 'OUT_DATE', 'STAT_CODE')):
            raise SyncError('출고 목록의 정상적인 빈 조회인지 확인할 수 없습니다.')
    rows = columns(values, HEADER, ('RULE_NO', 'OUT_NUM', 'OUT_DATE', 'STAT_CODE', 'TR_COMP_NM'), count,
                   optional=('TR_COMP_NM',))
    seen = set()
    for row in rows:
        try:
            day = date.fromisoformat(row['OUT_DATE'])
        except ValueError:
            raise SyncError('출고일 형식이 올바르지 않습니다.') from None
        if not start <= day <= end or row['RULE_NO'] in seen:
            raise SyncError('출고 조회 기간 또는 중복 전표를 확인하세요.')
        seen.add(row['RULE_NO'])
    # The observed popup has 25/50/100 page sizes, but no verified page request.
    # Fail closed at possible caps rather than claiming a complete daily snapshot.
    if count and count % 25 == 0:
        raise SyncError('출고 목록이 페이지 제한 건수에 도달했습니다. 전체 페이지 조회 확인 전에는 저장하지 않습니다.')
    return rows


def parse_detail(values, header):
    envelope(values)
    for field in ('RULE_NO', 'OUT_NUM', 'OUT_DATE', 'STAT_CODE'):
        if scalar(values, HEADER + field) != header[field]:
            raise SyncError('출고 목록과 상세 전표가 다릅니다. 같은 기간을 다시 조회하세요.')
    raw = scalar(values, DETAIL + 'RULE_NO')
    count = len(raw.split(',')) if raw else 0
    items = columns(values, DETAIL, ('RULE_NO', 'OUT_NO', 'ITEM_NUM', 'TR_QTY', 'BOX_QTY',
                    'IS_LOT', 'ORDER_NUM', 'CUS_RULE_NO'), count,
                    optional=('BOX_QTY', 'ORDER_NUM', 'CUS_RULE_NO', 'IS_LOT'))
    # PRODUCT_NM is not a key or a shipment validation field. Names come from
    # the MES item master; comma-delimited source names cannot be split safely.
    if not items:
        raise SyncError('출고 상세 품목이 없어 완전한 전표인지 확인할 수 없습니다.')
    by_key = {}
    for item in items:
        if item['RULE_NO'] in by_key or item['OUT_NO'] != header['RULE_NO']:
            raise SyncError('출고 상세행 식별값 또는 전표 연결이 올바르지 않습니다.')
        number(item['TR_QTY'])
        if item['BOX_QTY']:
            number(item['BOX_QTY'])  # Preserve fractional source values for partial/sample shipments.
        item['lots'] = []
        by_key[item['RULE_NO']] = item
    raw = scalar(values, LOT + 'RULE_NO')
    lots = columns(values, LOT, ('RULE_NO', 'LOT_TR_NO', 'ITEM_NUM', 'LOT_NUM', 'LOT_QTY', 'JOB_QTY',
                                 'TR_NO', 'DET_FIN_ITEM_NO'), len(raw.split(',')) if raw else 0,
                   optional=('TR_NO', 'DET_FIN_ITEM_NO'))
    trade_key = scalar(values, 'MES.Out.Set.TradeInfoSet.RULE_NO')
    seen = set()
    for lot in lots:
        item = by_key.get(lot['LOT_TR_NO'])
        identity = (lot['LOT_TR_NO'], lot['LOT_NUM'].upper())
        if (item is None or item['ITEM_NUM'] != lot['ITEM_NUM'] or identity in seen
                or (trade_key and lot['TR_NO'] and lot['TR_NO'] != trade_key)
                or (item['CUS_RULE_NO'] and lot['DET_FIN_ITEM_NO'] and item['CUS_RULE_NO'] != lot['DET_FIN_ITEM_NO'])):
            raise SyncError('출고 LOT의 품번·상세행 연결 또는 중복 여부를 확인하세요.')
        seen.add(identity)
        if number(lot['JOB_QTY']) > number(lot['LOT_QTY']):
            raise SyncError('LOT 배정수량이 LOT 수량을 초과합니다.')
        item['lots'].append(lot)
    warnings = []
    for item in items:
        qty = sum((number(l['JOB_QTY']) for l in item['lots']), Decimal(0))
        if qty > number(item['TR_QTY']):
            raise SyncError('LOT 배정수량 합계가 품번 출고수량을 초과합니다.')
        item['lot_complete'] = qty == number(item['TR_QTY']) and bool(item['lots'])
        if not item['lot_complete']:
            warnings.append('LOT 미배정 또는 배정수량 불일치 전표는 조회만 가능하며 LOT 출력에서 제외됩니다.')
    return {'header': header, 'items': items}, warnings


class ShipmentClient(ProductionClient):
    def fetch_day(self, day):
        self.query_errors = []
        # Pad bounds like existing adapters; keep only the requested day.
        start, end = day - timedelta(days=1), day + timedelta(days=1)
        payload = client_common(self) | self.context
        payload.update({'run.object.name': 'Common.Pop.OutDirect.OutDirectPopObj', 'jump.form.code': 'SEARCH'})
        payload.update({COND + k: '' for k in ('OUT_ITEM_COMP_NUM', 'OUT_ITEM_NUM', 'OUT_NO', 'OUT_NUM', 'PO_NUM', 'WARE_NO')})
        payload.update({COND+'OUT_DATE1': start.isoformat(), COND+'OUT_DATE2': end.isoformat()})
        listed = parse_list(query_with_retry(self.opener, payload), start, end)
        records, warnings = [], []
        for header in listed:
            if header['OUT_DATE'] != day.isoformat():
                continue
            if getattr(self, 'on_query_progress', None):
                self.on_query_progress()
            detail_payload = client_common(self) | self.context
            detail_payload.update({'run.object.name': 'MES.Out.Mana.OutObj', 'jump.form.code': 'Load'})
            detail_payload.update({HEADER+k: '' for k in ('APPLY_DATE','CONFIRM_DATE','EXT_RATE','EXT_TYPE','IS_EXCHANGE',
                'IS_FORWARD','LAST_DATE','MY_COMP_NO','PO_NUM','ROW_COUNT','RUN_DATE','STAT_CODE','STAT_CODE_NM',
                'TR_COMP_NM','TR_COMP_NO','WARE_NM','WARE_NO')})
            detail_payload.update({HEADER+k:header[k] for k in ('RULE_NO','OUT_NUM','OUT_DATE')})
            try:
                record, notes = parse_detail(query_with_retry(self.opener, detail_payload), header)
            except SyncError as exc:
                # Keep the rest of the day; the caller must not treat this as a full snapshot.
                self.query_errors.append({'date': day.isoformat(), 'shipment_no': header['OUT_NUM'],
                    'error': f"{day.isoformat()} 출고 {header['OUT_NUM']}: {exc}",
                    'diagnostic': {k: v for k, v in (exc.diagnostic or {}).items()
                        if k in ('field', 'expected_count', 'received_count', 'reason', 'empty_rows')}})
                continue
            records.append(record); warnings.extend(notes)
        return records, sorted(set(warnings))
