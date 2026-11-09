"""Read-only legacy subcontract dispatch list. Never creates local stock movements."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from services.production_sync_client import ProductionClient, SyncError, client_common, query_with_retry
from services.inbound_sync_client import is_test_partner

PREFIX='MES.Request.Set.EtcReqDetailListSet.'
COND='MES.Request.EtcReqDetailList.EtcReqDetailListCondSet.'
FIELDS=('RULE_NO','REQ_NUM','REQ_DATE_STR','REQ_COMP_NO','REQ_COMP_NM','ETC_REQ_TYPE','ETC_REQ_TYPE_NM','REQ_ITEM_NUM','ITEM_NUM','LOT_NUM','LOT_NO','ETC_REQ_QTY','ETC_IN_QTY','ETC_IN_DATETIME','STAT_CODE','STAT_CODE_NM','IN_WARE_NO','IN_WARE_NM')
PROCESSES={'PROC_SILVER','PROC_TAB'}
STATUSES={'REQ_OUT','REQ_IN','REQ_FIN'}


def unique_rows(rows):
    found={}
    for row in rows:
        key=row['RULE_NO']
        if key in found and found[key]!=row:
            raise SyncError('동일한 외주 출고 이력번호의 내용이 다릅니다. 저장을 중단했습니다.')
        found[key]=row
    return list(found.values())


def parse_response(values,start,end):
    def scalar(key):
        data=values.get(key,[''])
        if len(data)!=1:raise SyncError('외주 출고 응답에 중복 필드가 있습니다.')
        return data[0]
    if values.get('SO.NET.END')!=['SO.NET.END'] or any(k.startswith('THROW.') for k in values):
        raise SyncError('정상적인 외주 출고 조회 응답이 아닙니다.')
    if any(scalar(k) for k in ('jump.url','jump.object.name','jump.form.message')) or scalar('jump.form.code') not in ('','List'):
        raise SyncError('외주 출고 조회 대신 화면 전환 응답을 받았습니다.')
    if not (scalar('log.invoke.object').startswith('MES.Request.EtcReqDe') and scalar('MONITOR.QUERY.START') and scalar('MONITOR.QUERY.END')):
        raise SyncError('외주 출고 조회 완료를 확인할 수 없습니다.')
    anchor=scalar(PREFIX+'RULE_NO');declared=scalar(PREFIX+'ROW_COUNT') or scalar(PREFIX+'SQL_RESULT_CNT')
    if not anchor:
        if declared in ('','0') and not any(v for k,vs in values.items() if k.startswith(PREFIX) and k not in (PREFIX+'ROW_COUNT',PREFIX+'SQL_RESULT_CNT') for v in vs):return [],[]
        raise SyncError('외주 출고 응답의 이력번호가 없습니다.')
    count=len(anchor.split(','))
    if declared and (not declared.isdecimal() or int(declared)!=count):raise SyncError('외주 출고 표시 건수와 수신 건수가 다릅니다.')
    if not declared and count%25==0:raise SyncError('외주 출고 목록이 페이지 제한 건수에 도달했습니다. 전체 페이지 확인이 필요합니다.')
    arrays={}
    for field in FIELDS:
        raw=scalar(PREFIX+field);cells=raw.split(',') if raw else ['']*count
        if len(cells)!=count:raise SyncError(f'외주 출고 {field} 항목별 건수가 일치하지 않습니다.')
        arrays[field]=cells
    rows=[];excluded=0;outside=0
    for i in range(count):
        row={key:cells[i].strip() for key,cells in arrays.items()}
        if is_test_partner(row['REQ_COMP_NM']) or row['ETC_REQ_TYPE']=='PROC_PURCHA' or row['STAT_CODE']=='REQ_REG':
            excluded+=1;continue
        if row['ETC_REQ_TYPE'] not in PROCESSES or row['STAT_CODE'] not in STATUSES:
            raise SyncError('확인되지 않은 외주 가공유형 또는 출고 상태입니다.')
        if not all(row[k] for k in ('RULE_NO','REQ_NUM','REQ_COMP_NO','REQ_COMP_NM','REQ_ITEM_NUM','ITEM_NUM','LOT_NUM')):
            raise SyncError('외주 출고 이력번호·전표·거래처·품번·LOT가 누락되었습니다.')
        try:actual=date.fromisoformat(row['REQ_DATE_STR'])
        except ValueError as exc:raise SyncError('외주 출고일 형식을 확인해 주세요.') from exc
        for field in ('ETC_REQ_QTY','ETC_IN_QTY'):
            if field=='ETC_IN_QTY' and not row[field]:continue
            try:qty=Decimal(row[field])
            except InvalidOperation as exc:raise SyncError('외주 출고 수량이 숫자가 아닙니다.') from exc
            if not qty.is_finite() or qty<0 or qty>Decimal('1e15') or (field=='ETC_REQ_QTY' and qty==0):raise SyncError('외주 출고 수량이 유효하지 않습니다.')
            row[field]=str(qty)
        if not start<=actual<=end:outside+=1;continue
        rows.append(row)
    notes=[f'반제품 구매·가발주·테스트 거래처 {excluded}행 제외'] if excluded else []
    if outside:notes.append(f'조회 기간 밖 외주 출고 {outside}행 제외')
    return unique_rows(rows),notes


class SubcontractDispatchClient(ProductionClient):
    def fetch_range(self,start,end):
        rows=[];notes=[];cursor=start
        while cursor<=end:
            following=date(cursor.year+1,1,1) if cursor.month==12 else date(cursor.year,cursor.month+1,1)
            window_end=min(end,following-timedelta(days=1))
            callback=getattr(self,'on_query_progress',None)
            if callback:callback()
            payload=client_common(self)|self.context
            payload.update({'run.object.name':'MES.Request.EtcReqDetailList.EtcReqDetailListObj','jump.form.code':'List'})
            payload.update({COND+k:'' for k in ('ETC_REQ_TYPE','ITEM_NUM','REQ_COMP_NM','REQ_COMP_NO','STAT_CODE')})
            payload.update({COND+'REQ_DATE1':cursor.isoformat(),COND+'REQ_DATE2':window_end.isoformat()})
            found,warnings=parse_response(query_with_retry(self.opener,payload),cursor,window_end)
            rows.extend(found);notes.extend(warnings);cursor=following
        return unique_rows(rows),sorted(set(notes))

    def fetch_day(self,day):return self.fetch_range(day,day)
