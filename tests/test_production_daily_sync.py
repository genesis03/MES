from datetime import timedelta
import io
import json
import pytest
from test_production_sync import setup,DAY,row,response,add_item
from test_production_run_registration import prepare
from models.production_sync import ExternalProductionRecord
from models.production import ProductionWorkOrder
from services import production_sync_client as source,production_sync_service as sync
from services.production_daily_sync import MACHINES,ASSEMBLY_MACHINES,SHIFTS,COND,DATA,STOP,FAULT,parse_daily


def daily_response(day=DAY,machine=MACHINES[0],shift=SHIFTS[0],empty=False):
    name,identity,process,_=machine
    result={COND+'JOB_DATE':[day.isoformat()],COND+'MACH_NO':[identity],COND+'PROC_TYPE':[process],COND+'SHIFT_TYPE':[shift[0]],COND+'JOB_TYPE':['JOB_ORDER' if process=='PROC_CAP' else 'JOB_PLAN'],'SO.NET.END':['SO.NET.END'],'log.invoke.object':['MES.Product.RunDaily_123'],'MONITOR.QUERY.START':['start'],'MONITOR.QUERY.END':['end']}
    if empty:return result
    night=shift[2]=='NIGHT'
    prefix='LX' if name.startswith('LT') else 'LB'
    values=dict(ITEM_NUM='EXTERNAL-A',LOT_NUM=prefix+day.strftime('%Y%m%d')+'0'+str(int(name[-2:]))+('2' if night else '1'),RULE_NO='job-'+name+'-'+shift[2],JOB_PROD_QTY='284',JOB_FAULT_QTY='30',SET_UP_QTY='25',JOB_TIME_STRING='19:30 ~ 07:30' if night else '07:30 ~ 19:20',JOB_USER_NM='Worker',PRODUCT_NM='Test terminal',PREV_LOT_STR='RAW-1 : 172.0000\nRAW-2 : 22.0000')
    result.update({DATA+k:[v] for k,v in values.items()})
    result.update({STOP+k:[v] for k,v in dict(RULE_NO='stop-1',JOB_TIME_STRING='22:00 ~ 22:10' if night else '09:49 ~ 09:59',NON_OPER_TYPE='TOOL_CHANGE',NON_OPER_TYPE_NM='툴교환',JOB_PROD_NOTE='Change tool',Q_CHECK='□').items()})
    result.update({FAULT+k:[v] for k,v in dict(REL_NO=values['RULE_NO']+','+values['RULE_NO'],FAULT_TYPE='F2,F3',FAULT_TYPE_NM='긁힘,치수',FAULT_QTY='3,2').items()})
    return result


def test_daily_quantities_materials_stops_and_defects():
    rows,warnings=parse_daily(daily_response(),DAY,MACHINES[0],SHIFTS[0])
    data=rows[0]
    assert not warnings
    assert (data['JOB_QTY'],data['FAULT_QTY'],data['F10'],data['LOT_QTY'])==('284','5','25','314')
    assert data['SHIFT_TYPE']=='DAY' and data['OPERATOR_NAME']=='Worker'
    assert len(data['MATERIAL_LOTS'])==2
    assert data['DOWNTIMES'][0]['minutes']==10 and not data['DOWNTIMES'][0]['quality_confirmed']
    assert data['DEFECTS']==[{'code':'F2','name':'긁힘','qty':3.0},{'code':'F3','name':'치수','qty':2.0}]
    assert not {'PROC_QTY','RAW_QTY','ETC_QTY'} & data.keys()


def test_tapping_multiple_stops_and_zero_defects():
    values=daily_response(machine=MACHINES[-1]);values[DATA+'JOB_PROD_QTY']=['192'];values[DATA+'JOB_FAULT_QTY']=['2'];values[DATA+'SET_UP_QTY']=['2']
    values={k:v for k,v in values.items() if not k.startswith(FAULT)}
    values.update({STOP+k:[v] for k,v in dict(RULE_NO='s1,s2',JOB_TIME_STRING='15:45 ~ 16:00,16:00 ~ 16:05',NON_OPER_TYPE='CLEANING,TOOL_CHANGE',NON_OPER_TYPE_NM='청소,툴교환',JOB_PROD_NOTE='Clean,Tool',Q_CHECK='□,□').items()})
    data=parse_daily(values,DAY,MACHINES[-1],SHIFTS[0])[0][0]
    assert data['LOT_QTY']=='194' and data['FAULT_QTY']=='0'
    assert sum(d['minutes'] for d in data['DOWNTIMES'])==20
    assert not data['DEFECTS']


@pytest.mark.parametrize('change', [{DATA+'JOB_FAULT_QTY':['24']},{COND+'MACH_NO':['wrong']},{DATA+'JOB_PROD_QTY':['NaN']},{STOP+'NON_OPER_TYPE':['A,B']},{FAULT+'REL_NO':['wrong,wrong']},{DATA+'JOB_TIME_STRING':['25:00 ~ 26:00']}])
def test_daily_rejects_ambiguous_or_invalid_source(change):
    with pytest.raises(source.SyncError):parse_daily(daily_response()|change,DAY,MACHINES[0],SHIFTS[0])


def test_empty_daily_response_requires_completed_query():
    empty=daily_response(empty=True)
    assert parse_daily(empty,DAY,MACHINES[0],SHIFTS[0])==([],[])
    with pytest.raises(source.SyncError):parse_daily(empty|{'MONITOR.QUERY.END':['']},DAY,MACHINES[0],SHIFTS[0])


def test_period_client_queries_all_shifts_and_preserves_other_processes(monkeypatch):
    client=source.ProductionClient.__new__(source.ProductionClient);client.opener=object();client.context={}
    calls=[];renewals=[];client.on_query_progress=lambda:renewals.append(1)
    def post(_,payload):
        calls.append(payload)
        if payload['run.object.name'].endswith('ProcLotListObj'):
            return response([row(lot='ASSEMBLY-LOT',PROC_TYPE_NM='조립'),row(lot='OLD-LATHE',PROC_TYPE_NM='복합선반')])
        machine=next(m for m in MACHINES+ASSEMBLY_MACHINES if m[1]==payload[COND+'MACH_NO'])
        shift=next(s for s in SHIFTS if s[0]==payload[COND+'SHIFT_TYPE'])
        return daily_response(machine=machine,shift=shift,empty=machine[2]=='PROC_CAP')
    monkeypatch.setattr(source,'post',post)
    rows,warnings=client.fetch_day(DAY)
    assert len(calls)==19 and len(renewals)==19 and len(rows)==17 and not warnings
    assert len({r['LOT_NUM'] for r in rows})==17 and all(r['LOT_NUM']!='OLD-LATHE' for r in rows)
    assert {r['SHIFT_TYPE'] for r in rows if r.get('DAILY_SOURCE')}=={'DAY','NIGHT'}
    night=next(r for r in rows if r.get('SHIFT_TYPE')=='NIGHT')
    assert night['JOB_END_TIME'].startswith((DAY+timedelta(days=1)).strftime('%Y%m%d'))
    assert all(c[COND+'JOB_DATE']==DAY.isoformat() for c in calls if COND+'JOB_DATE' in c)


def test_daily_import_updates_existing_lot_without_duplicates_and_exports_report(setup):
    from models.production_sync import ProductionSyncItemMap
    from routers.daily_job_report import router
    from routers.internal_labels import router as labels
    from routers.production_extra import production_performance_status
    from test_production_sync import ADMIN
    order,_,_,equipment,_=prepare(setup);setup.app.include_router(router);setup.app.include_router(labels)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A',source_process_name='복합선반',item_id=item))
        db.add(ProductionSyncItemMap(source_part_no='LEGACY-FINISHED',source_process_name='복합선반',item_id=item));db.commit()
    values=daily_response()
    values.update({STOP+k:[v] for k,v in dict(RULE_NO='s1,s2',JOB_TIME_STRING='09:49 ~ 09:59,10:00 ~ 10:05',NON_OPER_TYPE='CLEANING,TOOL_CHANGE',NON_OPER_TYPE_NM='청소,툴교환',Q_CHECK='□,□',JOB_PROD_NOTE='Clean, floor, replace insert').items()})
    rows,_=parse_daily(values,DAY,MACHINES[0],SHIFTS[0]);lot=rows[0]['LOT_NUM']
    token,_=sync.claim(DAY,DAY,'MANUAL')
    sync.save_day([row(lot=lot,ITEM_NUM='LEGACY-FINISHED',PROC_TYPE_NM='복합선반')],token)
    with setup.sessions() as db:record_id=db.query(ExternalProductionRecord).one().id
    assert sync.save_day(rows,token)['updated']==1
    assert sync.save_day(rows,token)['unchanged']==1
    with setup.sessions() as db:
        assert db.query(ExternalProductionRecord).count()==1
        record=db.query(ExternalProductionRecord).one();assert record.id==record_id and record.fault_qty=='5'
        status=production_performance_status(start_date=DAY.isoformat(),end_date=DAY.isoformat(),performance_type=None,process_code=None,work_order_no=None,part_no=None,operator_name='Worker',db=db,current_user=ADMIN)
        assert status[0]['shift_name']=='주간' and status[0]['operator_name']=='Worker'
        assert status[0]['total_qty']==314 and len(status[0]['downtimes'])==2
    fetched=setup.client.get('/api/production/daily-job-report',params={'day':DAY.isoformat()}).json()['items'][0]
    assert fetched['defects'][0]['name']=='긁힘' and 'RAW-2' in fetched['material_lots']
    assert fetched['unassigned_downtime_note']=='Clean, floor, replace insert'
    export=setup.client.post('/api/production/daily-job-report/excel',json={'day':DAY.isoformat(),'keys':[fetched['key']]})
    assert export.status_code==200 and export.content.startswith(b'PK')
    from openpyxl import load_workbook
    workbook=load_workbook(io.BytesIO(export.content))
    assert any('Clean, floor, replace insert' in str(c.value) for sheet in workbook for cells in sheet for c in cells)
    printed=setup.client.get(f'/internal-labels/external-production/{record_id}')
    assert printed.status_code==200 and 'Worker' in printed.text and '主' not in printed.text


def test_single_downtime_note_with_commas_is_kept_whole():
    values=daily_response();note='인서트, 홀더, 체결상태 확인'
    values[STOP+'JOB_PROD_NOTE']=[note]
    rows,warnings=parse_daily(values,DAY,MACHINES[0],SHIFTS[0])
    assert rows[0]['DOWNTIMES'][0]['action']==note
    assert rows[0]['UNASSIGNED_DOWNTIME_NOTE']=='' and not warnings


def test_multiple_downtime_notes_with_ambiguous_commas_preserve_source_without_misassignment():
    values=daily_response();note='청소, 바닥 및 설비, 인서트 교체'
    values.update({STOP+k:[v] for k,v in dict(RULE_NO='stop-1,stop-2',JOB_TIME_STRING='09:49 ~ 09:59,10:00 ~ 10:05',NON_OPER_TYPE='CLEANING,TOOL_CHANGE',NON_OPER_TYPE_NM='청소,툴교환',Q_CHECK='□,□',JOB_PROD_NOTE=note).items()})
    rows,warnings=parse_daily(values,DAY,MACHINES[0],SHIFTS[0]);record=rows[0]
    assert record['UNASSIGNED_DOWNTIME_NOTE']==note
    assert [d['action'] for d in record['DOWNTIMES']]==['','']
    assert [d['minutes'] for d in record['DOWNTIMES']]==[10,5]
    assert record['FAULT_QTY']=='5' and record['LOT_QTY']=='314'
    assert len(warnings)==1 and '원문' in warnings[0]
    with pytest.raises(source.SyncError):
        parse_daily(values|{STOP+'JOB_TIME_STRING':['09:49 ~ 09:59']},DAY,MACHINES[0],SHIFTS[0])


@pytest.mark.parametrize('identity',['20260101080000000000001','20261231180000000000002'])
def test_work_and_downtime_identifiers_do_not_override_verified_query_date(identity):
    values=daily_response()
    values[DATA+'RULE_NO']=[identity]
    values[FAULT+'REL_NO']=[identity+','+identity]
    values[STOP+'RULE_NO']=[identity]
    rows,_=parse_daily(values,DAY,MACHINES[0],SHIFTS[0])
    data=rows[0]
    assert data['JOB_NUM']==identity
    assert data['JOB_ST_TIME']==DAY.strftime('%Y%m%d')+'073000'
    assert data['JOB_END_TIME']==DAY.strftime('%Y%m%d')+'192000'
    assert data['DOWNTIMES'][0]['started_at']==DAY.isoformat()+' 09:49'
    assert data['DOWNTIMES'][0]['minutes']==10
    assert data['LOT_QTY']=='314' and sum(d['qty'] for d in data['DEFECTS'])==5
    with pytest.raises(source.SyncError):
        parse_daily(values|{COND+'JOB_DATE':[(DAY-timedelta(days=1)).isoformat()]},DAY,MACHINES[0],SHIFTS[0])


@pytest.mark.parametrize('text,start_offset,end_offset,start_clock,end_clock',[
    ('19:30 ~ 07:30',0,1,'193000','073000'),
    ('03:30 ~ 07:30',1,1,'033000','073000'),
    ('23:50 ~ 00:10',0,1,'235000','001000'),
])
def test_night_times_follow_shift_and_midnight_without_id_timestamp(text,start_offset,end_offset,start_clock,end_clock):
    values=daily_response(shift=SHIFTS[1]);identity='20260101080000000000001'
    values[DATA+'RULE_NO']=[identity];values[FAULT+'REL_NO']=[identity+','+identity]
    values[DATA+'JOB_TIME_STRING']=[text]
    values[STOP+'RULE_NO']=[identity];values[STOP+'JOB_TIME_STRING']=['23:50 ~ 00:10']
    data=parse_daily(values,DAY,MACHINES[0],SHIFTS[1])[0][0]
    assert data['JOB_ST_TIME']==(DAY+timedelta(days=start_offset)).strftime('%Y%m%d')+start_clock
    assert data['JOB_END_TIME']==(DAY+timedelta(days=end_offset)).strftime('%Y%m%d')+end_clock
    stop=data['DOWNTIMES'][0]
    assert stop['started_at']==DAY.isoformat()+' 23:50'
    assert stop['ended_at']==(DAY+timedelta(days=1)).isoformat()+' 00:10'
    assert stop['minutes']==20


def assembly_response():
    values=daily_response(machine=ASSEMBLY_MACHINES[0])
    values={k:v for k,v in values.items() if not k.startswith((STOP,FAULT))}
    values.update({DATA+k:[v] for k,v in dict(ITEM_NUM='310061',LOT_NUM='',RULE_NO='assembly-first',JOB_PROD_QTY='4560',JOB_FAULT_QTY='0',SET_UP_QTY='0',JOB_TIME_STRING='08:00 ~ 18:00',PREV_LOT_STR='',JOB_USER_NM='Assembly worker').items()})
    values['MES.Product.RunDailyList.RunRelSet.REL_NO']=['assembly-first,assembly-second']
    return values


def assembly_lots():
    return [row(lot='LA'+DAY.strftime('%Y%m%d')+'01'+str(i),ITEM_NUM='310061',PROC_TYPE_NM='캡조립',JOB_QTY=str(qty),LOT_QTY=str(qty),FAULT_QTY='0',F10='0',JOB_ST_TIME=DAY.strftime('%Y%m%d')+start,JOB_END_TIME=DAY.strftime('%Y%m%d')+end) for i,qty,start,end in [(1,3120,'080000','173000'),(2,1440,'173000','180000')]]


def test_assembly_aggregate_supplements_original_lots_without_duplicating_quantities(monkeypatch):
    from services.production_daily_sync import supplement_assembly
    client=source.ProductionClient.__new__(source.ProductionClient);client.opener=object();client.context={}
    requested=[]
    def post(_,payload):
        requested.append(payload)
        return assembly_response() if payload[COND+'MACH_NM']=='AS-01' else daily_response(machine=ASSEMBLY_MACHINES[1],empty=True)
    monkeypatch.setattr(source,'post',post)
    legacy=assembly_lots();original=[dict(r) for r in legacy]
    assert not supplement_assembly(client,DAY,legacy)
    assert len(legacy)==2 and sum(int(r['JOB_QTY']) for r in legacy)==4560
    for before,after in zip(original,legacy):
        assert all(after[k]==before[k] for k in ('JOB_QTY','FAULT_QTY','F10','LOT_QTY','LOT_NUM','JOB_NUM','JOB_ST_TIME','JOB_END_TIME'))
        assert after['OPERATOR_NAME']=='Assembly worker' and after['SHIFT_TYPE']=='DAY'
        assert after['MACHINE_NAME']=='AS-01' and len(after['DAILY_REL_IDS'])==2
    assert all(p[COND+'JOB_TYPE']=='JOB_ORDER' and p[COND+'SHIFT_TYPE']=='DAY_SHIFT' for p in requested)


@pytest.mark.parametrize('change',[{'JOB_QTY':'3119'},{'JOB_ST_TIME':'20260325060000'},{'ITEM_NUM':'different'},{'LOT_NUM':'LA20260325021'}])
def test_assembly_mismatch_preserves_original_lots(monkeypatch,change):
    from services.production_daily_sync import supplement_assembly
    client=source.ProductionClient.__new__(source.ProductionClient);client.opener=object();client.context={}
    monkeypatch.setattr(source,'post',lambda _,payload:assembly_response() if payload[COND+'MACH_NM']=='AS-01' else daily_response(machine=ASSEMBLY_MACHINES[1],empty=True))
    legacy=assembly_lots();legacy[0].update(change);before=[dict(r) for r in legacy]
    warnings=supplement_assembly(client,DAY,legacy)
    assert len(warnings)==1 and legacy==before


def test_lotless_assembly_aggregate_is_not_accepted_as_machining():
    data,_=parse_daily(assembly_response(),DAY,ASSEMBLY_MACHINES[0],SHIFTS[0],allow_aggregate=True)
    assert data[0]['AGGREGATE_ONLY'] and data[0]['LOT_NUM']=='' and data[0]['JOB_QTY']=='4560'
    with pytest.raises(source.SyncError):parse_daily(assembly_response(),DAY,ASSEMBLY_MACHINES[0],SHIFTS[0])


def test_assembly_supplement_updates_two_existing_record_ids_without_new_lots(setup,monkeypatch):
    from services.production_daily_sync import supplement_assembly
    from models.production_sync import ProductionSyncItemMap
    prepare(setup)
    with setup.sessions() as db:
        item=add_item(db,part='310062-C',code='ASSY')
        db.add(ProductionSyncItemMap(source_part_no='310061',source_process_name='캡조립',item_id=item));db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL')
    legacy=assembly_lots();sync.save_day(legacy,token)
    with setup.sessions() as db:identities={r.lot_no:r.id for r in db.query(ExternalProductionRecord)}
    client=source.ProductionClient.__new__(source.ProductionClient);client.opener=object();client.context={}
    monkeypatch.setattr(source,'post',lambda _,payload:assembly_response() if payload[COND+'MACH_NM']=='AS-01' else daily_response(machine=ASSEMBLY_MACHINES[1],empty=True))
    assert not supplement_assembly(client,DAY,legacy)
    assert sync.save_day(legacy,token)['updated']==2
    assert sync.save_day(legacy,token)['unchanged']==2
    with setup.sessions() as db:
        records=db.query(ExternalProductionRecord).all()
        assert {r.lot_no:r.id for r in records}==identities
        assert sum(int(r.job_qty) for r in records)==4560
        assert all(json.loads(r.raw_json)['OPERATOR_NAME']=='Assembly worker' for r in records)
        assert all(r.lot_no and r.part_no=='310061' for r in records)


def zero_entry_response():
    day=__import__('datetime').date(2026,4,13)
    values=daily_response(day=day,machine=MACHINES[5],shift=SHIFTS[1])
    values={k:v for k,v in values.items() if not k.startswith(STOP)}
    fields=dict(ITEM_NUM='310201-A,310201-A',LOT_NUM=',LX20260414065',RULE_NO='2026041319284393600011884309384,2026041319291631200011994118210',JOB_PROD_QTY='0,127',JOB_FAULT_QTY='0,2',SET_UP_QTY='0,1',JOB_TIME_STRING='19:28 ~ 19:28,19:29 ~ 07:26',JOB_USER_NM='배기선,배기선',PRODUCT_NM='AX1-EV terminal,AX1-EV terminal',PREV_LOT_STR=',LR20260317992 : 31.1406')
    values.update({DATA+k:[v] for k,v in fields.items()})
    relation='2026041319291631200011994118210'
    values.update({FAULT+'REL_NO':[relation],FAULT+'FAULT_TYPE':['F4'],FAULT+'FAULT_TYPE_NM':['칩 엉김'],FAULT+'FAULT_QTY':['1']})
    values['MES.Product.RunDailyList.RunRelSet.REL_NO']=[fields['RULE_NO']]
    return values,day


def test_observed_zero_lot_row_is_skipped_and_real_night_performance_retained():
    values,day=zero_entry_response()
    rows,warnings=parse_daily(values,day,MACHINES[5],SHIFTS[1])
    assert len(rows)==1 and len(warnings)==1
    data=rows[0]
    assert data['LOT_NUM']=='LX20260414065'
    assert (data['JOB_QTY'],data['FAULT_QTY'],data['F10'],data['LOT_QTY'])==('127','1','1','129')
    assert data['JOB_NUM']=='2026041319291631200011994118210'
    assert data['JOB_TIME']=='20260413' and data['JOB_ST_TIME']=='20260413192900' and data['JOB_END_TIME']=='20260414072600'
    assert data['MACHINE_NO']=='6' and data['SHIFT_TYPE']=='NIGHT'
    assert data['MATERIAL_LOTS']==[{'lot_no':'LR20260317992','qty':'31.1406'}]
    assert data['DEFECTS']==[{'code':'F4','name':'칩 엉김','qty':1.0}]


@pytest.mark.parametrize('field,value',[
    ('JOB_PROD_QTY','1,127'),('JOB_FAULT_QTY','1,2'),
    ('JOB_TIME_STRING','19:28 ~ 19:29,19:29 ~ 07:26'),
    ('PREV_LOT_STR','RAW-USED : 1,LR20260317992 : 31.1406'),
])
def test_missing_lot_record_with_production_or_activity_still_blocks_and_captures_raw(field,value):
    values,day=zero_entry_response();values[DATA+field]=[value]
    with pytest.raises(source.SyncError,match='LOT_NUM') as error:parse_daily(values,day,MACHINES[5],SHIFTS[1])
    assert error.value.failure_data['fields'][DATA+'LOT_NUM']==[',LX20260414065']


def test_single_zero_lot_row_is_empty_performance_not_a_failed_day():
    values=daily_response();values={k:v for k,v in values.items() if not k.startswith((STOP,FAULT))}
    values.update({DATA+k:[v] for k,v in dict(LOT_NUM='',JOB_PROD_QTY='0',JOB_FAULT_QTY='0',SET_UP_QTY='0',JOB_TIME_STRING='19:28 ~ 19:28',PREV_LOT_STR='').items()})
    rows,warnings=parse_daily(values,DAY,MACHINES[0],SHIFTS[0])
    assert rows==[] and len(warnings)==1


def test_zero_entry_import_stores_only_real_lot_once(setup):
    values,day=zero_entry_response()
    rows,_=parse_daily(values,day,MACHINES[5],SHIFTS[1])
    token,_=sync.claim(day,day,'MANUAL')
    assert sync.save_day(rows,token)['inserted']==1
    assert sync.save_day(rows,token)['unchanged']==1
    with setup.sessions() as db:
        records=db.query(ExternalProductionRecord).all()
        assert len(records)==1
        record=records[0]
        assert record.lot_no=='LX20260414065' and record.work_date=='2026-04-13'
        assert (record.job_qty,record.fault_qty,record.lot_qty)==('127','1','129')
        assert json.loads(record.raw_json)['DEFECTS']==[{'code':'F4','name':'칩 엉김','qty':1.0}]
