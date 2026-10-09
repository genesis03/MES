"""Read-only PackList adapter using the shared UNI_MES login/session."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from services.production_sync_client import ProductionClient, SyncError, CONDITIONS, common, query_with_retry

PREFIX = 'Common.Set.LotSet.'


def parse_response(result, start, end):
    if result.get('SO.NET.END') != ['SO.NET.END'] or any(key.startswith('THROW.') for key in result):
        raise SyncError('정상적인 포장 조회 응답이 아닙니다.')
    if (any(result.get(key, [''])[0] for key in ('jump.url', 'jump.object.name', 'jump.form.message'))
            or result.get('jump.form.code', [''])[0] not in ('', 'List')):
        raise SyncError('포장 조회 대신 화면 전환 응답을 받았습니다. 로그인과 조회 권한을 확인해 주세요.')
    arrays = {key: result.get(PREFIX + key, [''])[0].split(',')
              for key in ('ITEM_NUM', 'LOT_NUM', 'CREATE_DATE', 'LOT_QTY', 'JOB_QTY', 'CONFIRM_DATE')}
    invocation = result.get('log.invoke.object', [''])[0]
    completed = (invocation.startswith(('MES.Stock.PackList.P_', 'MES.Stock.PackList.PackListObj_'))
                 and bool(result.get('MONITOR.QUERY.START', [''])[0])
                 and bool(result.get('MONITOR.QUERY.END', [''])[0]))
    declared = result.get(PREFIX + 'ROW_COUNT', [''])[0]
    if arrays['ITEM_NUM'] == ['']:
        dataset = [v for k, values in result.items() if k.startswith(PREFIX) and k != PREFIX + 'ROW_COUNT' for v in values]
        if completed and not any(dataset) and declared in ('', '0'):
            return [], []
        raise SyncError('포장 응답 항목이 없으며 정상적인 빈 조회인지 확인할 수 없습니다.')
    count = len(arrays['ITEM_NUM'])
    if any(len(values) != count for values in arrays.values()):
        raise SyncError('포장 항목별 건수가 일치하지 않아 저장을 중단했습니다.')
    if declared and (not declared.isdecimal() or int(declared) != count):
        raise SyncError('포장 서버 표시 건수와 수신 건수가 다릅니다. 페이지 처리를 확인해 주세요.')
    warnings = []
    for key in ('PRODUCT_NM', 'APPLY_COMP_NM'):
        values = result.get(PREFIX + key, [''])[0].split(',')
        if len(values) != count:
            values = [''] * count
            warnings.append('품명 또는 거래처 구분자가 모호한 항목은 원본 문자열 대신 공란으로 표시합니다.')
        arrays[key] = values
    rows = []
    for i in range(count):
        row = {key: values[i] for key, values in arrays.items()}
        if not row['ITEM_NUM'].strip() or not row['LOT_NUM'].strip():
            raise SyncError(f'{i + 1}행 품번 또는 LOT가 누락되었습니다.')
        try:
            actual = datetime.strptime(row['CREATE_DATE'], '%Y%m%d').date()
            if row['CONFIRM_DATE']:
                datetime.strptime(row['CONFIRM_DATE'], '%Y%m%d')
        except ValueError as exc:
            raise SyncError(f'{i + 1}행 포장일 또는 출고일 형식이 잘못되었습니다.') from exc
        if not start <= actual <= end:
            raise SyncError('요청한 기간과 반환된 포장일이 일치하지 않습니다.')
        for key in ('LOT_QTY', 'JOB_QTY'):
            try:
                number = Decimal(row[key])
            except InvalidOperation as exc:
                raise SyncError(f'{i + 1}행 포장·출고수량이 비어 있거나 숫자가 아닙니다.') from exc
            if not number.is_finite() or number < 0:
                raise SyncError(f'{i + 1}행 포장·출고수량이 유효하지 않습니다.')
        rows.append(row)
    return rows, warnings


class PackingClient(ProductionClient):
    def fetch_day(self, day):
        start, end = day - timedelta(days=1), day + timedelta(days=1)
        payload = common()
        payload.update({'MES.Product.Set.JobCondSet.' + key: '' for key in CONDITIONS})
        payload.update(self.context)
        payload.update({'run.object.name': 'MES.Stock.PackList.PackListObj', 'jump.form.code': 'List',
                        'MES.Product.Set.JobCondSet.JOB_DATE1': start.isoformat(),
                        'MES.Product.Set.JobCondSet.JOB_DATE2': end.isoformat()})
        rows, warnings = parse_response(query_with_retry(self.opener, payload), start, end)
        return [r for r in rows if r['CREATE_DATE'] == day.strftime('%Y%m%d')], warnings
