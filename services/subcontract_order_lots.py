"""Verified allocation LOTs returned by the legacy subcontract order detail."""
import json
from decimal import Decimal,InvalidOperation
from services.production_sync_client import SyncError

HEADER='MES.Request.Set.ReqInfoSet.'
DETAIL='MES.Request.Set.EtcReqDetailSet.'
LOTS='Common.Set.LotSet.'


def quantity(value,label,positive=False):
    try:result=Decimal(str(value))
    except InvalidOperation as exc:raise SyncError(f'{label} 수량이 숫자가 아닙니다.') from exc
    if not result.is_finite() or result<0 or result>Decimal('1e15') or positive and result==0:raise SyncError(f'{label} 수량이 유효하지 않습니다.')
    return format(result.normalize(),'f')


def allocations(record):
    raw=json.loads(record.raw_json)
    return raw.get('_ORDER_LOTS',[])


def require_allocations(rows):
    for row in rows:
        children=allocations(row)
        if not children:raise SyncError(f'{row.outbound_no} / {row.previous_part_no}: 발주에 배정된 개별 LOT가 없습니다. 해당 기간 외주출고를 다시 가져오세요.')
        if sum(Decimal(child['JOB_QTY']) for child in children)!=Decimal(row.outbound_qty):raise SyncError(f'{row.outbound_no}: 개별 LOT 배정수량과 출고수량이 다릅니다.')
    return [child for row in rows for child in allocations(row)]


def parse_detail(values,source_rows):
    def scalar(key):
        cells=values.get(key,[''])
        if len(cells)!=1:raise SyncError('외주 발주 상세 응답에 중복 필드가 있습니다.')
        return cells[0].strip()
    if not source_rows:raise SyncError('대조할 외주 출고 내역이 없습니다.')
    first=source_rows[0]
    if values.get('SO.NET.END')!=['SO.NET.END'] or any(k.startswith('THROW.') for k in values):raise SyncError('정상적인 외주 발주 상세 응답이 아닙니다.')
    if not scalar('log.invoke.object').startswith('MES.Request.Etc.EtcR') or not all(scalar(k) for k in ('MONITOR.QUERY.START','MONITOR.QUERY.END')):raise SyncError('외주 발주 상세 조회 완료를 확인할 수 없습니다.')
    if scalar('jump.form.code') not in ('','List') or any(scalar(k) for k in ('jump.url','jump.object.name','jump.form.message')):raise SyncError('발주 상세 대신 화면 전환 응답을 받았습니다.')
    for field in ('REQ_NUM','REQ_COMP_NO','ETC_REQ_TYPE','STAT_CODE'):
        if scalar(HEADER+field)!=first[field]:raise SyncError(f'{first["REQ_NUM"]}: 발주 상세의 {field}가 출고 내역과 다릅니다.')
    master_key=scalar(HEADER+'RULE_NO')
    if not master_key:raise SyncError('외주 발주 상세의 발주 식별키를 확인할 수 없습니다.')
    def arrays(prefix,anchor,fields):
        raw=scalar(prefix+anchor)
        if not raw:raise SyncError('외주 발주의 개별 LOT 상세가 비어 있습니다.')
        count=len(raw.split(','));result={}
        declared=scalar(prefix+'ROW_COUNT')
        if declared and (not declared.isdecimal() or int(declared)!=count):raise SyncError('외주 발주 상세 표시 건수와 수신 건수가 다릅니다.')
        for field in fields:
            raw=scalar(prefix+field);cells=raw.split(',') if raw else ['']*count
            if len(cells)!=count:raise SyncError(f'외주 발주 {field} 항목별 건수가 다릅니다.')
            result[field]=[cell.strip() for cell in cells]
        return [{key:column[i] for key,column in result.items()} for i in range(count)]
    details=arrays(DETAIL,'RULE_NO',('RULE_NO','ITEM_NUM','ITEM_NO','LOT_NUM','LOT_NO','ETC_REQ_QTY','ETC_REQ_TYPE','REQ_ITEM_NUM'))
    by_key={r['RULE_NO']:r for r in details}
    if len(by_key)!=len(details) or set(by_key)!={r['RULE_NO'] for r in source_rows}:raise SyncError('발주 상세 품목과 외주 출고 품목의 이력번호가 다릅니다.')
    parts={}
    for source in source_rows:
        detail=by_key[source['RULE_NO']]
        for field in ('ITEM_NUM','LOT_NUM','LOT_NO','ETC_REQ_TYPE','REQ_ITEM_NUM'):
            if detail[field]!=source[field]:raise SyncError('발주 상세 품목·묶음 LOT와 외주 출고 내역이 다릅니다.')
        if Decimal(quantity(detail['ETC_REQ_QTY'],'발주',True))!=Decimal(source['ETC_REQ_QTY']):raise SyncError('발주 상세 수량과 출고수량이 다릅니다.')
        key=(detail['ITEM_NUM'],detail['ITEM_NO'])
        if key in parts:raise SyncError('같은 품번의 발주 상세가 여러 행이라 개별 LOT를 연결할 수 없습니다.')
        parts[key]=source['RULE_NO']
    boxes=arrays(LOTS,'RULE_NO',('RULE_NO','LOT_NUM','ITEM_NUM','ITEM_NO','JOB_QTY','LOT_QTY','REST_QTY'))
    grouped={key:[] for key in by_key};seen_keys=set();seen_lots=set()
    for box in boxes:
        if not all(box[key] for key in ('RULE_NO','LOT_NUM','ITEM_NUM','ITEM_NO')):raise SyncError('개별 발주 LOT의 식별번호·품번이 없습니다.')
        if box['RULE_NO'] in seen_keys or box['LOT_NUM'] in seen_lots:raise SyncError('발주에 배정된 개별 LOT가 중복되었습니다.')
        seen_keys.add(box['RULE_NO']);seen_lots.add(box['LOT_NUM'])
        parent=parts.get((box['ITEM_NUM'],box['ITEM_NO']))
        if parent is None:raise SyncError('개별 LOT가 해당 발주 품목에 속하지 않습니다.')
        box['JOB_QTY']=quantity(box['JOB_QTY'],'개별 LOT 배정',True)
        box['LOT_QTY']=quantity(box['LOT_QTY'],'원 LOT',True)
        box['REST_QTY']=quantity(box['REST_QTY'],'원 LOT 잔량')
        if Decimal(box['JOB_QTY'])>Decimal(box['LOT_QTY']):raise SyncError('개별 LOT 배정수량이 원 LOT 수량보다 큽니다.')
        grouped[parent].append(box)
    result=[]
    for source in source_rows:
        assigned=grouped[source['RULE_NO']]
        if not assigned or sum(Decimal(box['JOB_QTY']) for box in assigned)!=Decimal(source['ETC_REQ_QTY']):raise SyncError(f'{source["ITEM_NUM"]}: 개별 LOT 배정수량 합계와 출고수량이 다릅니다.')
        result.append(dict(source,_ORDER_RULE_NO=master_key,_ORDER_LOTS=assigned))
    return result


def validate_allocations(source):
    if '_ORDER_LOTS' not in source:return
    children=source['_ORDER_LOTS']
    if not isinstance(children,list) or not children:raise SyncError('개별 발주 LOT 목록이 비어 있습니다.')
    seen=set();keys=set();total=Decimal(0)
    for child in children:
        if not isinstance(child,dict) or not all(child.get(k) for k in ('RULE_NO','LOT_NUM','ITEM_NUM','ITEM_NO')):raise SyncError('개별 발주 LOT 식별정보가 없습니다.')
        if child['ITEM_NUM']!=source['ITEM_NUM'] or child['LOT_NUM'] in seen or child['RULE_NO'] in keys:raise SyncError('개별 발주 LOT의 품번 또는 중복 여부를 확인하세요.')
        seen.add(child['LOT_NUM']);keys.add(child['RULE_NO'])
        for field,positive in (('JOB_QTY',True),('LOT_QTY',True),('REST_QTY',False)):
            if field not in child:raise SyncError('개별 발주 LOT 수량 항목이 없습니다.')
            quantity(child[field],field,positive)
        if Decimal(child['JOB_QTY'])>Decimal(child['LOT_QTY']):raise SyncError('개별 LOT 배정수량이 원 LOT 수량보다 큽니다.')
        total+=Decimal(child['JOB_QTY'])
    if total!=Decimal(source['ETC_REQ_QTY']):raise SyncError('개별 발주 LOT 배정수량 합계와 출고수량이 다릅니다.')


def fetch_detail(client,source_rows,order_rule_no):
    """Use the observed master key; list-row RULE_NO belongs to a different table."""
    import re
    from services.production_sync_client import client_common,query_with_retry
    if not re.fullmatch(r'[0-9]{1,100}',str(order_rule_no or '')):raise SyncError('외주 발주 헤더 식별키가 필요합니다. 출고 품목행의 RULE_NO를 대신 사용할 수 없습니다.')
    if not source_rows:raise SyncError('대조할 외주 출고 내역이 없습니다.')
    first=source_rows[0]
    callback=getattr(client,'on_query_progress',None)
    if callback:callback()
    payload=client_common(client)|client.context
    payload.update({'run.object.name':'MES.Request.Etc.EtcReqObj','jump.form.code':'List'})
    header=getattr(client,'order_lookup_headers',{}).get(str(order_rule_no),{})
    payload.update({HEADER+'RULE_NO':str(order_rule_no),HEADER+'REQ_NUM':first['REQ_NUM'],HEADER+'REQ_COMP_NO':first['REQ_COMP_NO'],HEADER+'REQ_DATE':header.get('REQ_DATE',first['REQ_DATE_STR']),HEADER+'ETC_REQ_TYPE':first['ETC_REQ_TYPE'],HEADER+'REQ_TYPE':first['ETC_REQ_TYPE'],HEADER+'STAT_CODE':first['STAT_CODE']})
    values=query_with_retry(client.opener,payload)
    if values.get(HEADER+'RULE_NO')!=[str(order_rule_no)]:raise SyncError('발주 상세 응답의 헤더 식별키가 요청과 다릅니다.')
    return parse_detail(values,source_rows)


CONDITION='MES.Request.Set.ReqCondSet.'


def lookup_header(client,source_rows):
    """Observed read-only CHECK maps the document number to its master RULE_NO."""
    import re
    from services.production_sync_client import client_common,query_with_retry
    first=source_rows[0]
    callback=getattr(client,'on_query_progress',None)
    if callback:callback()
    payload=client_common(client)|client.context
    payload.update({'run.object.name':'Common.Pop.Request.EtcReqPopObj','jump.form.code':'CHECK'})
    fields=('CONFIRM_DATE','ETC_REQ_TYPE','FIELD_REQ_NUM','FIELD_RULE_NO','IS_CHANGE','IS_ETCINFORM','IS_UNMATCH_INQTY','IS_UNOUT','ORDER_NO','ORDER_NUM','PO_NUM','REQ_COMP_NM','REQ_COMP_NO','REQ_DATE1','REQ_DATE2','REQ_ITEM_NUM','REQ_NO','REQ_NUM','REQ_TYPE','STAT_CODE','YEAR')
    payload.update({CONDITION+field:'' for field in fields})
    payload.update({CONDITION+'ETC_REQ_TYPE':"'PROC_HEAT_OUT', 'PROC_MACH_OUT', 'PROC_SILVER_OUT', 'PROC_PURCHA'",CONDITION+'FIELD_REQ_NUM':'MES.Request.Etc.EtcReqForm.ReqInfoModel.REQ_NUM',CONDITION+'FIELD_RULE_NO':'MES.Request.Etc.EtcReqForm.ReqInfoModel.RULE_NO',CONDITION+'REQ_NUM':first['REQ_NUM']})
    code_keys='PROC_MIL PROC_HEAT PROC_GRIND PROC_MACH PROC_COMB PROC_TAB PROC_WIRE PROC_CHO PROC_SILVER PROC_PURCHA PROC_EDIS PROC_AUX PROC_DOT'.split()
    names='밀링 열처리 1차연삭 머시닝가공 복합선반 탭핑가공 와이어컷팅 초경 은도금 반제품구매 방전 부속가공 DOT'.split()
    prefix='Common.System.Code.CodeShortSet.'
    payload.update({prefix+'CODE_KEY':','.join(code_keys),prefix+'CODE_NM':','.join(names),prefix+'CODE_VALUE':','.join(['ETCREQ']*13),prefix+'GROUP_KEY':','.join(['PROC_TYPE']*13),prefix+'PAR_KEY':','*12,prefix+'SEQ_NO':'1,2,3,8,12,13,16,17,18,19,20,26,27'})
    # Date controls belong to the popup's screen state. An order can predate
    # its dispatch, so the exact document lookup must not inherit those dates.
    def check(values):
        def scalar(key):
            data=values.get(key,[''])
            if len(data)!=1:raise SyncError('발주번호 확인 응답에 중복 필드가 있습니다.')
            return data[0].strip()
        if values.get('SO.NET.END')!=['SO.NET.END'] or any(key.startswith('THROW.') for key in values):raise SyncError('정상적인 발주번호 확인 응답이 아닙니다.')
        if not scalar('log.invoke.object').startswith('Common.Pop.Request.E') or not all(scalar(k) for k in ('MONITOR.QUERY.START','MONITOR.QUERY.END')):raise SyncError('발주번호 확인 완료를 확인할 수 없습니다.')
        if scalar('jump.form.code') not in ('','CHECK') or any(scalar(k) for k in ('jump.url','jump.object.name','jump.form.message')):raise SyncError('발주번호 확인 대신 화면 전환 응답을 받았습니다.')
        return scalar
    values=query_with_retry(client.opener,payload);scalar=check(values)
    count=scalar(HEADER+'ROW_COUNT')
    if count=='0' or (not count and not scalar(HEADER+'RULE_NO')):
        # Old processing categories can also be hidden by the popup's type filter.
        # Retry the same exact document number, never a whole-history import.
        if callback:callback()
        payload[CONDITION+'ETC_REQ_TYPE']=''
        values=query_with_retry(client.opener,payload);scalar=check(values)
        count=scalar(HEADER+'ROW_COUNT')
    if count=='0':raise SyncError('발주번호 확인 조회가 0건입니다. 날짜·가공유형 제한을 해제한 조회에서도 해당 발주를 반환하지 않았습니다.')
    if count not in ('','1'):
        if count.isdecimal() and len(count)<=8:raise SyncError(f'발주번호 확인 조회가 {count}건입니다. 발주를 하나로 연결할 수 없습니다.')
        raise SyncError('발주번호 확인 응답의 조회 건수 값이 유효하지 않습니다.')
    # Some older records omit ROW_COUNT. Scalar identity plus full detail-line
    # validation is sufficient; do not reject an otherwise verified order.
    for field in ('REQ_NUM','REQ_COMP_NO','ETC_REQ_TYPE','STAT_CODE'):
        if scalar(HEADER+field)!=first[field]:
            if field=='REQ_NUM':
                # Diagnostic metadata only: never log the raw response or login context.
                raw=scalar(HEADER+'REQ_NUM')
                if HEADER+'REQ_NUM' not in values:returned='필드 누락'
                elif not raw:returned='빈값'
                elif re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',raw):returned=raw
                else:returned='형식 확인 필요'
                row_count=count if count.isdecimal() and len(count)<=8 else '미제공'
                has_key=bool(re.fullmatch(r'[0-9]{1,100}',scalar(HEADER+'RULE_NO')))
                diagnostic=dict(error_kind='order_number_mismatch',expected_req_num=first['REQ_NUM'],returned_req_num=returned,row_count=row_count,header_key_present=has_key)
                raise SyncError(f'발주번호 불일치: 요청 {first["REQ_NUM"]} / 응답 {returned} / 조회건수 {row_count} / 발주키 {"있음" if has_key else "없음"}. 다른 발주를 연결하지 않고 중단했습니다.',diagnostic=diagnostic)
            raise SyncError(f'{first["REQ_NUM"]}: 발주번호 확인 응답의 {field}가 출고 내역과 다릅니다.')
    key=scalar(HEADER+'RULE_NO')
    if not re.fullmatch(r'[0-9]{1,100}',key):raise SyncError('발주 헤더 식별키가 유효하지 않습니다(조회 건수 누락 여부와 원본 응답을 확인하세요).')
    cache=getattr(client,'order_lookup_headers',{})
    cache[key]={'REQ_DATE':scalar(HEADER+'REQ_DATE')[:10]}
    client.order_lookup_headers=cache
    return key
