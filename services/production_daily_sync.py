"""Observed read-only RunDaily protocol, normalized into existing external LOT records."""
import re
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from services import production_sync_client as protocol

MACHINES=(
    ('LT-01','2020031014205554200011131630374','PROC_COMB','복합선반'),
    ('LT-02','2020031014205555800010181577976','PROC_COMB','복합선반'),
    ('LT-03','202003101433251200011130523307','PROC_COMB','복합선반'),
    ('LT-04','202006231557108100011125217881','PROC_COMB','복합선반'),
    ('LT-05','202006231557108100010465387980','PROC_COMB','복합선반'),
    ('LT-06','2024013017154867900011669483228','PROC_COMB','복합선반'),
    ('TP-01','2020031014335540200010964464978','PROC_TAB','탭핑가공'),
    ('TP-02','2020031014335541700011791136815','PROC_TAB','탭핑가공'),
)
ASSEMBLY_MACHINES=(
    ('AS-01','2021120908460199500011225493216','PROC_CAP','캡조립'),
    ('AS-02','2021120908460199500011374320574','PROC_CAP','캡조립'),
)
SHIFTS=(('DAY_SHIFT','주간','DAY'),('NIGHT_SHIFT','야간','NIGHT'))
COND='MES.Product.Set.RunDailyCondSet.'
DATA='MES.Product.Set.RunDailySet.'
STOP='MES.Product.Set.RunNonOperSet.'
FAULT='Common.Set.FaultHisSet.'


def scalar(values,key):
    items=values.get(key,[''])
    if len(items)!=1:raise protocol.SyncError('작업일보 응답 항목 형식을 확인할 수 없습니다.')
    return items[0]


def quantity(value,label):
    try:number=Decimal(value)
    except (InvalidOperation,ValueError):raise protocol.SyncError(label+' 수량을 확인할 수 없습니다.')
    if not number.is_finite() or number<0:raise protocol.SyncError(label+' 수량이 유효하지 않습니다.')
    return number


def columns(values,prefix,keys,count,required=(),free_text=()):
    result={}
    for key in keys:
        value=scalar(values,prefix+key)
        pieces=value.split(',') if value else ['']*count
        if key in free_text:
            if count==1:pieces=[value]
            elif len(pieces)!=count:pieces=['']*count
        if len(pieces)!=count:raise protocol.SyncError('작업일보 '+key+' 항목별 건수가 일치하지 않습니다. 해당 날짜는 저장하지 않습니다.')
        if key in required and not all(p.strip() for p in pieces):raise protocol.SyncError('작업일보 '+key+' 필수 항목이 없습니다.')
        result[key]=pieces
    return result


def time_range(value,day,night=False):
    # Only the echoed work date and shift anchor times; RULE_NO is an opaque identifier.
    match=re.fullmatch(r'\s*(\d{2}:\d{2})\s*~\s*(\d{2}:\d{2})\s*',value)
    if not match:raise protocol.SyncError('작업일보 시작·종료시간 형식이 잘못되었습니다.')
    try:
        start=datetime.combine(day,datetime.strptime(match[1],'%H:%M').time())
        end=datetime.combine(day,datetime.strptime(match[2],'%H:%M').time())
    except ValueError:raise protocol.SyncError('작업일보 시작·종료시간 값이 잘못되었습니다.')
    if night and start.hour<12:start+=timedelta(days=1);end+=timedelta(days=1)
    if end<start:end+=timedelta(days=1)
    return start,end


def _parse_daily(values,day,machine,shift,allow_aggregate=False):
    name,machine_id,process,process_name=machine
    shift_code,shift_name,shift_type=shift
    for key,expected in [('JOB_DATE',day.isoformat()),('MACH_NO',machine_id),('PROC_TYPE',process),('SHIFT_TYPE',shift_code)]:
        if scalar(values,COND+key)!=expected:raise protocol.SyncError('작업일보 조회 조건과 응답 조건이 일치하지 않습니다.')
    if any(scalar(values,k) for k in ('jump.url','jump.object.name','jump.form.message')) or scalar(values,'jump.form.code') not in ('','List'):
        raise protocol.SyncError('작업일보 대신 화면 전환 응답을 받았습니다.')
    lot_value=scalar(values,DATA+'LOT_NUM')
    aggregate=allow_aggregate and process=='PROC_CAP' and not lot_value and bool(scalar(values,DATA+'ITEM_NUM'))
    if allow_aggregate and scalar(values,COND+'JOB_TYPE')!='JOB_ORDER':raise protocol.SyncError('조립 작업일보 조회 유형이 일치하지 않습니다.')
    if not lot_value and not scalar(values,DATA+'ITEM_NUM') and not aggregate:
        complete=scalar(values,'log.invoke.object').startswith('MES.Product.RunDaily_') and scalar(values,'MONITOR.QUERY.START') and scalar(values,'MONITOR.QUERY.END')
        if not complete or any(scalar(values,DATA+k) for k in ['ITEM_NUM','JOB_PROD_QTY','RULE_NO']):
            raise protocol.SyncError('작업일보 응답이 정상적인 빈 조회인지 확인할 수 없습니다.')
        return [],[]
    count=len((scalar(values,DATA+'ITEM_NUM') if aggregate or not lot_value else lot_value).split(','))
    keys='ITEM_NUM LOT_NUM RULE_NO JOB_PROD_QTY JOB_FAULT_QTY SET_UP_QTY JOB_TIME_STRING JOB_USER_NM PRODUCT_NM PREV_LOT_STR'.split()
    arrays=columns(values,DATA,keys,count,required=[k for k in keys[:7] if k!='LOT_NUM' and not (allow_aggregate and k=='JOB_TIME_STRING')],free_text=['PRODUCT_NM'])
    raw_product=scalar(values,DATA+'PRODUCT_NM')
    unassigned_product=raw_product if count>1 and raw_product and len(raw_product.split(','))!=count else ''
    related_ids=scalar(values,'MES.Product.RunDailyList.RunRelSet.REL_NO').split(',')
    allowed_relations=set(arrays['RULE_NO'])|({r for r in related_ids if r} if aggregate and count==1 else set())
    # Stops belong to this machine/day/shift, rather than an arbitrary item row.
    stop_ids=scalar(values,STOP+'RULE_NO');stops=[];warnings=[]
    if unassigned_product:warnings.append('품명 구분자가 모호한 날짜는 내부 품목 마스터의 품명을 표시합니다.')
    stop_count=len(stop_ids.split(',')) if stop_ids else 0
    raw_note=scalar(values,STOP+'JOB_PROD_NOTE')
    unassigned_note=raw_note if raw_note and (not stop_count or stop_count>1 and len(raw_note.split(','))!=stop_count) else ''
    if unassigned_note:warnings.append('일부 비가동 비고는 구분자가 모호하여 개별 연결 없이 원문으로 보관했습니다.')
    if stop_ids:
        fields=columns(values,STOP,['RULE_NO','JOB_TIME_STRING','NON_OPER_TYPE','NON_OPER_TYPE_NM','JOB_PROD_NOTE','Q_CHECK'],len(stop_ids.split(',')),required=['RULE_NO','JOB_TIME_STRING','NON_OPER_TYPE'],free_text=['JOB_PROD_NOTE'])
        for i,identity in enumerate(fields['RULE_NO']):
            a,b=time_range(fields['JOB_TIME_STRING'][i],day,shift_type=='NIGHT')
            stops.append(dict(id=identity,type_code=fields['NON_OPER_TYPE'][i],type_name=fields['NON_OPER_TYPE_NM'][i],started_at=a.strftime('%Y-%m-%d %H:%M'),ended_at=b.strftime('%Y-%m-%d %H:%M'),minutes=(b-a).total_seconds()/60,action=fields['JOB_PROD_NOTE'][i],quality_confirmed=fields['Q_CHECK'][i] in ('☑','Y','1')))
    defects=[];fault_ids=scalar(values,FAULT+'REL_NO')
    if fault_ids:
        fields=columns(values,FAULT,['REL_NO','FAULT_TYPE','FAULT_TYPE_NM','FAULT_QTY'],len(fault_ids.split(',')),required=['REL_NO','FAULT_TYPE','FAULT_QTY'])
        for i,rel in enumerate(fields['REL_NO']):
            if rel not in allowed_relations:raise protocol.SyncError('불량 상세를 해당 작업에 연결할 수 없습니다.')
            defects.append(dict(rel_no=rel,code=fields['FAULT_TYPE'][i],name=fields['FAULT_TYPE_NM'][i],qty=float(quantity(fields['FAULT_QTY'][i],'불량 상세'))))
    rows=[]
    for i in range(count):
        good=quantity(arrays['JOB_PROD_QTY'][i],'양품');combined=quantity(arrays['JOB_FAULT_QTY'][i],'불량·셋업');setup=quantity(arrays['SET_UP_QTY'][i],'셋업')
        if combined<setup:raise protocol.SyncError('불량·셋업 합계가 셋업 수량보다 작아 저장하지 않습니다.')
        defect=combined-setup;rel=arrays['RULE_NO'][i]
        details=[{k:v for k,v in d.items() if k!='rel_no'} for d in defects if d['rel_no']==rel or aggregate and count==1]
        if allow_aggregate and not arrays['JOB_TIME_STRING'][i].strip():
            a=b=None
        else:
            a,b=time_range(arrays['JOB_TIME_STRING'][i],day,shift_type=='NIGHT')
        if not arrays['LOT_NUM'][i].strip():
            empty=good==combined==setup==0 and a==b and not arrays['PREV_LOT_STR'][i].strip() and not any(d['qty'] for d in details)
            if empty:
                warnings.append('LOT 없는 0수량·0분 기록은 생산실적에서 제외했습니다.')
                continue
            if not aggregate:raise protocol.SyncError('작업일보 LOT_NUM 필수 항목이 없습니다. 수량·시간이 있는 기록이라 저장하지 않습니다.')
        materials=[]
        for line in arrays['PREV_LOT_STR'][i].splitlines():
            if not line.strip():continue
            lot,sep,qty=line.rpartition(':')
            if not sep or not lot.strip():raise protocol.SyncError('투입 LOT·수량 형식을 확인할 수 없습니다.')
            materials.append(dict(lot_no=lot.strip(),qty=str(quantity(qty.strip(),'투입 LOT'))))
        rows.append(dict(ITEM_NUM=arrays['ITEM_NUM'][i],PRODUCT_NM=arrays['PRODUCT_NM'][i],PROC_TYPE_NM=process_name,JOB_TIME=day.strftime('%Y%m%d'),JOB_ST_TIME=a.strftime('%Y%m%d%H%M%S') if a else '',JOB_END_TIME=b.strftime('%Y%m%d%H%M%S') if b else '',JOB_NUM=rel,LOT_NUM=arrays['LOT_NUM'][i],JOB_QTY=str(good),LOT_QTY=str(good+combined),FAULT_QTY=str(defect),F10=str(setup),DAILY_SOURCE=True,SHIFT_TYPE=shift_type,SHIFT_NAME=shift_name,OPERATOR_NAME=arrays['JOB_USER_NM'][i],MACHINE_NAME=name,MACHINE_NO=str(int(name.split('-')[1])),MATERIAL_LOTS=materials,DOWNTIMES=stops,UNASSIGNED_DOWNTIME_NOTE=unassigned_note,UNASSIGNED_PRODUCT_NAME=unassigned_product,DEFECTS=details,SOURCE_FAULT_WITH_SETUP=str(combined),DAILY_REL_IDS=[r for r in related_ids if r],AGGREGATE_ONLY=aggregate))
    return rows,warnings


def parse_daily(values,day,machine,shift,allow_aggregate=False):
    try:
        return _parse_daily(values,day,machine,shift,allow_aggregate)
    except protocol.SyncError as exc:
        # Retain only the observed production datasets, never login context, cookies or server diagnostics.
        prefixes=(COND,DATA,STOP,FAULT,'MES.Product.RunDailyList.RunRelSet.')
        exc.failure_data=dict(work_date=day.isoformat(),equipment=machine[0],process=machine[3],shift=shift[1],
            error=str(exc),assembly=allow_aggregate,fields={k:v for k,v in values.items() if k.startswith(prefixes) and not any(part in k.upper() for part in ('PASSWORD','USER_PASS','COOKIE','TOKEN','SECRET'))})
        raise


def record_query_error(client,day,label,exc):
    if not hasattr(client,'query_errors'):client.query_errors=[]
    client.query_errors.append(dict(error=day.isoformat()+' 조회: '+label+': '+str(exc),failure_data=getattr(exc,'failure_data',None)))


def fetch_day(client,day):
    rows=[];warnings=[]
    for machine in MACHINES:
        for shift in SHIFTS:
            callback=getattr(client,'on_query_progress',None)
            if callback:callback()
            name,identity,process,_=machine
            payload=protocol.client_common(client);payload.update(client.context)
            payload.update({'run.object.name':'MES.Product.RunDailyList.RunDailyListObj','jump.form.code':'',COND+'JOB_DATE':day.isoformat(),COND+'JOB_TYPE':'JOB_PLAN',COND+'MACH_NM':name,COND+'MACH_NO':identity,COND+'PROC_TYPE':process,COND+'SHIFT_TYPE':shift[0],COND+'SHIFT_TYPE_NM':shift[1]})
            try:
                data,notes=parse_daily(protocol.query_with_retry(client.opener,payload),day,machine,shift)
            except protocol.SyncError as exc:
                record_query_error(client,day,name+' '+shift[1],exc)
                continue
            rows.extend(data);warnings.extend(notes)
    return rows,sorted(set(warnings))


def supplement_assembly(client,day,legacy):
    """Enrich matching existing assembly lots; never store a LOT-less aggregate as production."""
    from services.production_sync_view import external_machine_no
    from services.production_sync_mapping import stage_suffix
    warnings=[]
    for machine in ASSEMBLY_MACHINES:
        shift=SHIFTS[0]  # Only assembly day-shift requests have been observed.
        callback=getattr(client,'on_query_progress',None)
        if callback:callback()
        name,identity,process,_=machine
        payload=protocol.client_common(client);payload.update(client.context)
        payload.update({'run.object.name':'MES.Product.RunDailyList.RunDailyListObj','jump.form.code':'',COND+'JOB_DATE':day.isoformat(),COND+'JOB_TYPE':'JOB_ORDER',COND+'MACH_NM':name,COND+'MACH_NO':identity,COND+'PROC_TYPE':process,COND+'SHIFT_TYPE':shift[0],COND+'SHIFT_TYPE_NM':shift[1]})
        try:
            aggregates,notes=parse_daily(protocol.query_with_retry(client.opener,payload),day,machine,shift,allow_aggregate=True)
        except protocol.SyncError as exc:
            record_query_error(client,day,name+' '+shift[1],exc)
            continue
        warnings.extend(notes)
        used=set()
        for aggregate in aggregates:
            candidates=[]
            for row in legacy:
                if stage_suffix(row['PROC_TYPE_NM'])!='-C' or row['ITEM_NUM'].strip().casefold()!=aggregate['ITEM_NUM'].strip().casefold() or external_machine_no(row['LOT_NUM'])!=aggregate['MACHINE_NO']:
                    continue
                if aggregate['LOT_NUM'] and row['LOT_NUM'].strip().upper()!=aggregate['LOT_NUM'].strip().upper():continue
                # The old LOT timestamps must be contained in the observed assembly work period.
                if aggregate['JOB_ST_TIME']:
                    if not row['JOB_ST_TIME'] or not row['JOB_END_TIME'] or not (aggregate['JOB_ST_TIME']<=row['JOB_ST_TIME']<=row['JOB_END_TIME']<=aggregate['JOB_END_TIME']):continue
                else:
                    # Without times, only an unambiguous item aggregate with matching quantities can enrich LOTs.
                    if sum(a['ITEM_NUM'].strip().casefold()==aggregate['ITEM_NUM'].strip().casefold() for a in aggregates)!=1:continue
                    if row.get('SHIFT_TYPE') and row['SHIFT_TYPE']!=aggregate['SHIFT_TYPE']:continue
                candidates.append(row)
            quantities=('JOB_QTY','FAULT_QTY','F10')
            if not candidates or any(sum(Decimal(row[key]) for row in candidates)!=Decimal(aggregate[key]) for key in quantities) or any(row['LOT_NUM'] in used for row in candidates):
                warnings.append(name+' 조립 작업일보와 LOT별 수량·시간을 연결할 수 없어 기존 실적을 유지했습니다.')
                continue
            for row in candidates:
                used.add(row['LOT_NUM'])
                for key in ('DAILY_SOURCE','SHIFT_TYPE','SHIFT_NAME','OPERATOR_NAME','MACHINE_NAME','MACHINE_NO','DOWNTIMES','UNASSIGNED_DOWNTIME_NOTE','UNASSIGNED_PRODUCT_NAME','DAILY_REL_IDS'):
                    row[key]=aggregate[key]
                row['ASSEMBLY_DAILY_SUPPLEMENT']=True
                # Aggregated material usage and defects cannot be allocated to individual production lots.
                row['ASSEMBLY_GROUP_LOTS']=[r['LOT_NUM'] for r in candidates]
                if len(candidates)==1:
                    row['MATERIAL_LOTS']=aggregate['MATERIAL_LOTS'];row['DEFECTS']=aggregate['DEFECTS']
                else:
                    row['ASSEMBLY_GROUP_MATERIALS']=aggregate['MATERIAL_LOTS'];row['ASSEMBLY_GROUP_DEFECTS']=aggregate['DEFECTS']
    return sorted(set(warnings))
