"""Read-only adapter for the observed confirmed receipt list."""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from services.production_sync_client import ProductionClient, SyncError, client_common, query_with_retry

PREFIX='MES.In.InDetailList.InDetailListSet.'
COND='MES.In.InDetailList.InDetailListCondSet.'
FIELDS=('IN_NUM','HIS_NUM','ITEM_NUM','REQ_COMP_NM','IN_DATE','CONFIRM_DATE','CONFIRM_QTY','IS_CONFIRM','IN_STATE','PRODUCT_TYPE_NM','MATERIAL_TYPE_NM')


def is_test_partner(value):
    return re.sub(r'\s+','',value or '')=='테스트거래처'


def source_date(value):
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2})(?:\s+(?:오전|오후)\s+\d{1,2}:\d{2}:\d{2})?',value.strip())
    if not match:raise SyncError('입고일 또는 확정일 형식을 확인해 주세요.')
    try:return date.fromisoformat(match[1])
    except ValueError as exc:raise SyncError('입고일 또는 확정일이 유효하지 않습니다.') from exc


def parse_response(values,start,end):
    def scalar(key):
        data=values.get(key,[''])
        if len(data)!=1:raise SyncError('입고 응답에 중복 필드가 있습니다.')
        return data[0]
    if values.get('SO.NET.END')!=['SO.NET.END'] or any(k.startswith('THROW.') for k in values):raise SyncError('정상적인 입고 조회 응답이 아닙니다.')
    if any(scalar(k) for k in ('jump.url','jump.object.name','jump.form.message')) or scalar('jump.form.code') not in ('','List'):raise SyncError('입고 조회 대신 화면 전환 응답을 받았습니다.')
    if not (scalar('log.invoke.object').startswith('MES.In.InConfirmList') and scalar('MONITOR.QUERY.START') and scalar('MONITOR.QUERY.END')):raise SyncError('입고 조회 완료를 확인할 수 없습니다.')
    raw=scalar(PREFIX+'ITEM_NUM');declared=scalar(PREFIX+'ROW_COUNT') or scalar(PREFIX+'SQL_RESULT_CNT')
    if not raw:
        if declared in ('','0') and not any(v for k,vs in values.items() if k.startswith(PREFIX) and k not in (PREFIX+'ROW_COUNT',PREFIX+'SQL_RESULT_CNT') for v in vs):return [],[]
        raise SyncError('입고 응답이 정상적인 빈 목록인지 확인할 수 없습니다.')
    count=len(raw.split(','));arrays={}
    if declared and (not declared.isdecimal() or int(declared)!=count):raise SyncError('입고 표시 건수와 수신 건수가 다릅니다.')
    # A page request has not been verified; do not silently accept possible caps.
    if not declared and count%25==0:raise SyncError('입고 목록이 페이지 제한 건수에 도달했습니다. 전체 페이지 확인 전에는 저장하지 않습니다.')
    for field in FIELDS:
        text=scalar(PREFIX+field)
        cells=text.split(',') if text else ['']*count
        if len(cells)!=count:raise SyncError(f'입고 {field} 항목별 건수가 일치하지 않습니다.')
        arrays[field]=cells
    rows=[];excluded=0
    for i in range(count):
        row={key:cells[i].strip() for key,cells in arrays.items()}
        if is_test_partner(row['REQ_COMP_NM']):excluded+=1;continue
        if not all(row[k] for k in ('IN_NUM','HIS_NUM','ITEM_NUM','REQ_COMP_NM','CONFIRM_DATE')):raise SyncError('입고번호·이력번호·품번·거래처·확정일이 누락되었습니다.')
        actual=source_date(row['IN_DATE'])
        if not start<=actual<=end:raise SyncError('입고 응답 일자가 요청 기간과 다릅니다.')
        row['IN_DATE']=actual.isoformat();row['CONFIRM_DATE']=source_date(row['CONFIRM_DATE']).isoformat()
        if row['IS_CONFIRM']!='Y' or row['IN_STATE']!='IN_PROCESS':raise SyncError('입고 완료·확정 상태가 아닌 자료가 포함되어 저장을 중단했습니다.')
        try:qty=Decimal(row['CONFIRM_QTY'])
        except InvalidOperation as exc:raise SyncError('입고 확정수량이 숫자가 아닙니다.') from exc
        if not qty.is_finite() or qty<0 or qty>Decimal('1e15'):raise SyncError('입고 확정수량이 유효하지 않습니다.')
        row['CONFIRM_QTY']=str(qty)
        rows.append(row)
    return rows,([f'테스트 거래처 {excluded}행 제외'] if excluded else [])


class InboundClient(ProductionClient):
    def fetch_day(self,day):
        start,end=day-timedelta(days=1),day+timedelta(days=1)
        payload=client_common(self)|self.context
        payload.update({'run.object.name':'MES.In.InConfirmList.InConfirmListObj','jump.form.code':'List'})
        payload.update({COND+k:'' for k in ('IN_STATE','ITEM_NUM','REQ_COMP_NO','REQ_TYPE','SAMPLE_NO','STAT_CODE','WARE_NO')})
        payload.update({COND+'IN_DATE1':start.isoformat(),COND+'IN_DATE2':end.isoformat()})
        rows,notes=parse_response(query_with_retry(self.opener,payload),start,end)
        return [r for r in rows if r['IN_DATE']==day.isoformat()],notes
