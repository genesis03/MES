"""Read-only EtcFaultList List adapter. No production or stock writes."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from services.production_sync_client import ProductionClient, SyncError, CONDITIONS, client_common, query_with_retry

PREFIX = 'Common.Set.LotSet.'
QUANTITY_FIELDS = ('ORI_LOT_QTY', 'JOB_QTY', 'LOT_QTY', 'KIPUP_QTY', 'SAMPLE_QTY', 'LOSS_QTY', 'REST_QTY')
FAULT_FIELDS = tuple(f'F{i}' for i in range(1, 14))
IDENTITY_FIELDS = ('IN_NUM', 'ITEM_NUM', 'LOT_NUM', 'PROC_TYPE_NM')


def amount(value):
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise SyncError('외주불량 수량이 숫자가 아닙니다.') from exc
    if not result.is_finite() or result < 0 or result > Decimal('1e15'):
        raise SyncError('외주불량 수량 범위를 확인해 주세요.')
    return result


def parse_response(result, start, end):
    def scalar(key):
        values = result.get(key, [''])
        if len(values) != 1:
            raise SyncError('외주불량 응답의 중복 항목은 행 연결을 확인할 수 없습니다.')
        return values[0]
    if result.get('SO.NET.END') != ['SO.NET.END'] or any(k.startswith('THROW.') for k in result):
        raise SyncError('정상적인 외주불량 조회 응답이 아닙니다.')
    if any(scalar(k) for k in ('jump.url', 'jump.object.name', 'jump.form.message')) or scalar('jump.form.code') not in ('', 'List'):
        raise SyncError('외주불량 조회 대신 화면 전환 응답을 받았습니다.')
    completed = (scalar('log.invoke.object').startswith('MES.QC.EtcFaultList.')
                 and scalar('MONITOR.QUERY.START') and scalar('MONITOR.QUERY.END'))
    if not completed:
        raise SyncError('외주불량 목록 조회 완료를 확인할 수 없습니다.')
    identity = scalar(PREFIX + 'ITEM_NUM')
    declared = scalar(PREFIX + 'ROW_COUNT')
    if not identity:
        if declared in ('', '0') and not any(v for k, values in result.items() if k.startswith(PREFIX) and k != PREFIX + 'ROW_COUNT' for v in values):
            return [], []
        raise SyncError('외주불량 품번이 누락되어 정상적인 빈 목록인지 확인할 수 없습니다.')
    count = len(identity.split(','))
    if declared and (not declared.isdecimal() or int(declared) != count):
        raise SyncError('외주불량 표시 건수와 수신 건수가 다릅니다. 페이지 처리를 확인해 주세요.')
    required = (*IDENTITY_FIELDS, 'CONFIRM_DATE', 'FAULT_QTY')
    optional = (*QUANTITY_FIELDS, *FAULT_FIELDS, 'PRODUCT_NM', 'APPLY_COMP_NM', 'ORDER_COMP_NM', 'CUS_LOT_NUM')
    arrays, warnings = {}, []
    for field in (*required, *optional):
        value = scalar(PREFIX + field)
        pieces = value.split(',') if value else [''] * count
        if len(pieces) != count:
            if field in ('PRODUCT_NM', 'APPLY_COMP_NM', 'ORDER_COMP_NM'):
                warnings.append('구분자가 모호한 품명·거래처는 내부 품목명 또는 공란으로 표시합니다.')
                pieces = [''] * count
            else:
                raise SyncError(f'외주불량 {field} 항목별 건수가 일치하지 않습니다.')
        arrays[field] = pieces
    rows = []
    for index in range(count):
        row = {key: values[index].strip() for key, values in arrays.items()}
        if any(not row[k] for k in required):
            raise SyncError(f'외주불량 {index + 1}행의 필수 항목이 없습니다.')
        try:
            actual = datetime.strptime(row['CONFIRM_DATE'], '%Y-%m-%d').date()
        except ValueError as exc:
            raise SyncError('외주불량 일자 형식이 잘못되었습니다.') from exc
        if not start <= actual <= end:
            raise SyncError('외주불량 응답 일자가 요청 기간과 다릅니다.')
        for key in ('FAULT_QTY', *QUANTITY_FIELDS, *FAULT_FIELDS):
            if row[key] != '':
                amount(row[key])
        if sum(amount(row[k] or '0') for k in FAULT_FIELDS) != amount(row['FAULT_QTY']):
            warnings.append('일부 LOT의 원본 불량 합계와 유형별 합계가 다릅니다. 원본 수량을 보존했습니다.')
        rows.append(row)
    return rows, sorted(set(warnings))


class SubcontractFaultClient(ProductionClient):
    def fetch_day(self, day):
        start, end = day - timedelta(days=1), day + timedelta(days=1)
        payload = client_common(self)
        payload.update({'MES.Product.Set.JobCondSet.' + key: '' for key in CONDITIONS})
        payload.update(self.context)
        payload.update({'run.object.name': 'MES.QC.EtcFaultList.EtcFaultListObj', 'jump.form.code': 'List',
                        'MES.Product.Set.JobCondSet.JOB_DATE1': start.isoformat(),
                        'MES.Product.Set.JobCondSet.JOB_DATE2': end.isoformat()})
        rows, warnings = parse_response(query_with_retry(self.opener, payload), start, end)
        return [r for r in rows if r['CONFIRM_DATE'] == day.isoformat()], warnings
