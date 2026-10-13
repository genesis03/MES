from datetime import timedelta
import io
import json
import pytest
from test_production_sync import setup,DAY,row,response,add_item
from test_production_run_registration import prepare
from models.production_sync import ExternalProductionRecord
from models.production import ProductionWorkOrder
from services import production_sync_client as source,production_sync_service as sync
from services.production_daily_sync import MACHINES,SHIFTS,COND,DATA,STOP,FAULT,parse_daily


def daily_response(day=DAY,machine=MACHINES[0],shift=SHIFTS[0],empty=False):
    name,identity,process,_=machine
    result={COND+'JOB_DATE':[day.isoformat()],COND+'MACH_NO':[identity],COND+'PROC_TYPE':[process],COND+'SHIFT_TYPE':[shift[0]],'SO.NET.END':['SO.NET.END'],'log.invoke.object':['MES.Product.RunDaily_123'],'MONITOR.QUERY.START':['start'],'MONITOR.QUERY.END':['end']}
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
        machine=next(m for m in MACHINES if m[1]==payload[COND+'MACH_NO'])
        shift=next(s for s in SHIFTS if s[0]==payload[COND+'SHIFT_TYPE'])
        return daily_response(machine=machine,shift=shift)
    monkeypatch.setattr(source,'post',post)
    rows,warnings=client.fetch_day(DAY)
    assert len(calls)==17 and len(renewals)==17 and len(rows)==17 and not warnings
    assert len({r['LOT_NUM'] for r in rows})==17 and all(r['LOT_NUM']!='OLD-LATHE' for r in rows)
    assert {r['SHIFT_TYPE'] for r in rows if r.get('DAILY_SOURCE')}=={'DAY','NIGHT'}
    night=next(r for r in rows if r.get('SHIFT_TYPE')=='NIGHT')
    assert night['JOB_END_TIME'].startswith((DAY+timedelta(days=1)).strftime('%Y%m%d'))
    assert all(c[COND+'JOB_DATE']==DAY.isoformat() for c in calls[:-1])


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
    rows,_=parse_daily(daily_response(),DAY,MACHINES[0],SHIFTS[0]);lot=rows[0]['LOT_NUM']
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
        assert status[0]['total_qty']==314 and len(status[0]['downtimes'])==1
    fetched=setup.client.get('/api/production/daily-job-report',params={'day':DAY.isoformat()}).json()['items'][0]
    assert fetched['defects'][0]['name']=='긁힘' and 'RAW-2' in fetched['material_lots']
    export=setup.client.post('/api/production/daily-job-report/excel',json={'day':DAY.isoformat(),'keys':[fetched['key']]})
    assert export.status_code==200 and export.content.startswith(b'PK')
    printed=setup.client.get(f'/internal-labels/external-production/{record_id}')
    assert printed.status_code==200 and 'Worker' in printed.text and '主' not in printed.text
