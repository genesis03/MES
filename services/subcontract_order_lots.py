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
    """Resolve the master key using the captured numbered CHECK request."""
    from datetime import date
    import re
    from services.production_sync_client import client_common,query_with_retry
    if not source_rows:raise SyncError('대조할 외주 출고 내역이 없습니다.')
    first=source_rows[0]
    callback=getattr(client,'on_query_progress',None)
    if callback:callback()
    start,end=getattr(client,'order_search_range',(date.fromisoformat(first['REQ_DATE_STR']),date.fromisoformat(first['REQ_DATE_STR'])))
    payload=client_common(client)|client.context
    payload.update({'run.object.name':'Common.Pop.Request.EtcReqPopObj','jump.form.code':'CHECK'})
    fields=('CONFIRM_DATE','ETC_REQ_TYPE','FIELD_REQ_NUM','FIELD_RULE_NO','IS_CHANGE','IS_ETCINFORM','IS_UNMATCH_INQTY','IS_UNOUT','ORDER_NO','ORDER_NUM','PO_NUM','REQ_COMP_NM','REQ_COMP_NO','REQ_DATE1','REQ_DATE2','REQ_ITEM_NUM','REQ_NO','REQ_NUM','REQ_TYPE','STAT_CODE','YEAR')
    payload.update({CONDITION+field:'' for field in fields})
    payload.update({CONDITION+'ETC_REQ_TYPE':"'PROC_HEAT_OUT', 'PROC_MACH_OUT', 'PROC_SILVER_OUT', 'PROC_PURCHA'",CONDITION+'FIELD_REQ_NUM':'MES.Request.Etc.EtcReqForm.ReqInfoModel.REQ_NUM',CONDITION+'FIELD_RULE_NO':'MES.Request.Etc.EtcReqForm.ReqInfoModel.RULE_NO',CONDITION+'REQ_NUM':first['REQ_NUM'],CONDITION+'REQ_DATE1':start.isoformat(),CONDITION+'REQ_DATE2':end.isoformat()})
    code_keys='PROC_MIL PROC_HEAT PROC_GRIND PROC_MACH PROC_COMB PROC_TAB PROC_WIRE PROC_CHO PROC_SILVER PROC_PURCHA PROC_EDIS PROC_AUX PROC_DOT'.split()
    names='밀링 열처리 1차연삭 머시닝가공 복합선반 탭핑가공 와이어컷팅 초경 은도금 반제품구매 방전 부속가공 DOT'.split()
    prefix='Common.System.Code.CodeShortSet.'
    payload.update({prefix+'CODE_KEY':','.join(code_keys),prefix+'CODE_NM':','.join(names),prefix+'CODE_VALUE':','.join(['ETCREQ']*13),prefix+'GROUP_KEY':','.join(['PROC_TYPE']*13),prefix+'PAR_KEY':','*12,prefix+'SEQ_NO':'1,2,3,8,12,13,16,17,18,19,20,26,27'})
    values=query_with_retry(client.opener,payload)
    def scalar(key):
        cells=values.get(key,[''])
        if len(cells)!=1:raise SyncError('발주 목록 응답에 중복 필드가 있습니다.')
        return cells[0].strip()
    if values.get('SO.NET.END')!=['SO.NET.END'] or any(key.startswith('THROW.') for key in values):raise SyncError('정상적인 발주 목록 응답이 아닙니다.')
    if not scalar('log.invoke.object').startswith('Common.Pop.Request.E') or not all(scalar(k) for k in ('MONITOR.QUERY.START','MONITOR.QUERY.END')):raise SyncError('발주번호 확인(CHECK) 완료를 확인할 수 없습니다.')
    if scalar('jump.form.code') not in ('','CHECK') or any(scalar(k) for k in ('jump.url','jump.object.name','jump.form.message')):raise SyncError('발주 목록 대신 화면 전환 응답을 받았습니다.')
    raw=scalar(HEADER+'REQ_NUM')
    if not raw:raise SyncError(f'{first["REQ_NUM"]}: 발주번호 확인(CHECK) 응답에 발주번호가 없습니다.')
    count=len(raw.split(','));columns={}
    for field in ('REQ_NUM','RULE_NO','REQ_COMP_NO','ETC_REQ_TYPE','STAT_CODE','REQ_DATE'):
        raw=scalar(HEADER+field);cells=raw.split(',') if raw else ['']*count
        if len(cells)!=count:raise SyncError(f'발주 목록 {field} 항목별 건수가 다릅니다.')
        columns[field]=[cell.strip() for cell in cells]
    declared=scalar(HEADER+'ROW_COUNT')
    if declared and (not declared.isdecimal() or int(declared)!=count):raise SyncError('발주 목록 표시 건수와 수신 건수가 다릅니다.')
    candidates=[i for i,number in enumerate(columns['REQ_NUM']) if number==first['REQ_NUM']]
    if not candidates:raise SyncError(f'{first["REQ_NUM"]}: 검색된 발주 목록 {count}건에 요청한 발주번호가 없습니다.')
    candidates=[i for i in candidates if columns['REQ_COMP_NO'][i]==first['REQ_COMP_NO'] and columns['ETC_REQ_TYPE'][i]==first['ETC_REQ_TYPE']]
    if len(candidates)!=1:raise SyncError(f'{first["REQ_NUM"]}: 발주번호·거래처·가공유형이 일치하는 발주가 {len(candidates)}건입니다.')
    index=candidates[0]
    if columns['STAT_CODE'][index]!=first['STAT_CODE']:raise SyncError(f'{first["REQ_NUM"]}: 발주 목록의 상태가 출고 내역과 다릅니다.')
    key=columns['RULE_NO'][index]
    if not re.fullmatch(r'[0-9]{1,100}',key):raise SyncError('발주 목록의 헤더 식별키가 유효하지 않습니다.')
    cache=getattr(client,'order_lookup_headers',{})
    cache[key]={'REQ_DATE':columns['REQ_DATE'][index][:10]}
    client.order_lookup_headers=cache
    return key
