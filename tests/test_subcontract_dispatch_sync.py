import copy
import json
from datetime import date,timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_production_sync import setup,DAY
from core.security import get_current_user
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record,SubcontractDispatchSyncState as State,SubcontractDispatchSyncRun as Run
from models.subcontract_outbound import SubcontractOutboundMaster
from models.subcontract_inbound import SubcontractInboundMaster
from models.production_lot import ProductionLotModel
from routers.subcontract_outbound import router as outbound_router
from routers.subcontract_dispatch_sync import router,records
from routers.external_sync import router as common_router
from services import production_sync_service as sync
from services import subcontract_dispatch_client as client
from services.subcontract_dispatch_store import save_range
from services.production_sync_client import SyncError


def row(day=DAY,**changes):
    result={k:'' for k in client.FIELDS}
    return result|dict(RULE_NO='line1',REQ_NUM='R001',REQ_DATE_STR=day.isoformat(),REQ_COMP_NO='vendor1',REQ_COMP_NM='혜성금속',ETC_REQ_TYPE='PROC_SILVER',ETC_REQ_TYPE_NM='은도금',REQ_ITEM_NUM='310060',ITEM_NUM='310060-B',LOT_NUM='LS001',ETC_REQ_QTY='100',STAT_CODE='REQ_OUT',STAT_CODE_NM='출고')|changes


def response(rows):
    return {client.PREFIX+k:[','.join(r[k] for r in rows)] for k in client.FIELDS}|{'SO.NET.END':['SO.NET.END'],'log.invoke.object':['MES.Request.EtcReqDe_test'],'MONITOR.QUERY.START':['s'],'MONITOR.QUERY.END':['e']}


def test_observed_response_only_physical_dispatch_and_actual_date():
    values=json.loads((Path(__file__).parent/'fixtures/subcontract_dispatch_list.json').read_text())
    rows,notes=client.parse_response(values,date(2026,10,1),date(2026,11,9))
    assert len(rows)==39 and len({r['RULE_NO'] for r in rows})==39
    assert notes==['반제품 구매·가발주·테스트 거래처 8행 제외']
    pending=[r for r in rows if r['STAT_CODE']=='REQ_OUT']
    assert len(pending)==6 and sum(int(r['ETC_REQ_QTY']) for r in pending)==14429
    assert next(r for r in rows if r['REQ_NUM']=='R202611072')['REQ_DATE_STR']=='2026-11-05'
    assert any(r['STAT_CODE']=='REQ_IN' and not r['ETC_IN_QTY'] for r in rows)


@pytest.mark.parametrize('changes',[{'ETC_REQ_QTY':'NaN'},{'ETC_REQ_QTY':'-1'},{'ETC_REQ_QTY':'0'},{'ETC_REQ_QTY':'1e100'},{'ETC_IN_QTY':'bad'},{'REQ_DATE_STR':'2026-02-30'},{'LOT_NUM':''},{'RULE_NO':''},{'STAT_CODE':'UNKNOWN'},{'ETC_REQ_TYPE':'UNKNOWN'},{'REQ_COMP_NM':'comma,vendor'}])
def test_invalid_response_fails(changes):
    with pytest.raises(SyncError):client.parse_response(response([row(**changes)]),DAY,DAY)


def test_empty_truncated_duplicate_and_outside_range():
    assert client.parse_response(response([]),DAY,DAY)==([],[])
    bad=response([row()]);del bad['MONITOR.QUERY.END']
    with pytest.raises(SyncError):client.parse_response(bad,DAY,DAY)
    with pytest.raises(SyncError,match='페이지'):client.parse_response(response([row(RULE_NO=str(i)) for i in range(25)]),DAY,DAY)
    assert len(client.parse_response(response([row(),row()]),DAY,DAY)[0])==1
    with pytest.raises(SyncError,match='이력번호'):client.parse_response(response([row(),row(ETC_REQ_QTY='99')]),DAY,DAY)
    rows,notes=client.parse_response(response([row(),row(DAY-timedelta(days=1),RULE_NO='old')]),DAY,DAY)
    assert len(rows)==1 and '기간 밖' in notes[0]


def test_range_request_is_read_only_uses_current_auth_and_exact_windows(monkeypatch):
    instance=client.SubcontractDispatchClient.__new__(client.SubcontractDispatchClient);instance.context={};instance.opener=object();calls=[]
    monkeypatch.setattr(client,'client_common',lambda _: {'ENCRYPT.KEY':'current'})
    def query(opener,payload):
        calls.append(payload)
        return response([row(date.fromisoformat(payload[client.COND+'REQ_DATE1']),RULE_NO=str(len(calls)))])
    monkeypatch.setattr(client,'query_with_retry',query)
    rows,_=instance.fetch_range(date(2026,10,1),date(2026,11,9))
    assert len(rows)==2 and len(calls)==2
    assert [p[client.COND+'REQ_DATE2'] for p in calls]==['2026-10-31','2026-11-09']
    assert all(p['run.object.name']=='MES.Request.EtcReqDetailList.EtcReqDetailListObj' and p['jump.form.code']=='List' and p['ENCRYPT.KEY']=='current' for p in calls)


def test_idempotent_import_history_detail_and_no_inventory_side_effects(setup):
    setup.app.include_router(router);setup.app.include_router(outbound_router)
    token,_=sync.claim(DAY,DAY,'MANUAL','subcontract_dispatch')
    rows=[row(),row(RULE_NO='line2',LOT_NUM='LS002',ETC_REQ_QTY='200')]
    assert save_range(rows,token,DAY,DAY)['inserted']==2
    assert save_range(rows,token,DAY,DAY)['unchanged']==2
    rows[0]['ETC_REQ_QTY']='90'
    assert save_range(rows,token,DAY,DAY)['updated']==1
    assert save_range([],token,DAY,DAY)['received']==0
    with setup.sessions() as db:
        assert db.query(Record).count()==2
        assert db.query(SubcontractOutboundMaster).count()==0
        assert db.query(SubcontractInboundMaster).count()==0
        assert db.query(ProductionLotModel).count()==0
    result=setup.client.get('/api/subcontract/outbound/history').json()
    assert len(result['items'])==1
    group=result['items'][0];assert group['source']=='EXTERNAL' and group['item_count']==1
    detail=setup.client.get(f"/api/subcontract/outbound/{group['outbound_id']}").json()
    assert detail['items'][0]['outbound_qty']=='290'
    assert len(detail['items'][0]['lots'])==2 and detail['status']=='EXTERNAL'
    assert setup.client.post(f"/api/subcontract/outbound/{group['outbound_id']}/cancel").status_code==404
    assert setup.client.get('/api/subcontract/outbound/history',params={'partner_name':'other'}).json()['total']==0
    assert setup.client.get('/api/subcontract/outbound/history',params={'start_date':(DAY+timedelta(days=1)).isoformat()}).json()['total']==0
    with setup.sessions() as db:
        db.get(State,1).lease_token='new';db.commit()
    with pytest.raises(SyncError):save_range(rows,token,DAY,DAY)


def test_records_permissions_common_settings_and_template(setup):
    setup.app.include_router(router);setup.app.include_router(common_router)
    token,_=sync.claim(DAY,DAY,'MANUAL','subcontract_dispatch');save_range([row()],token,DAY,DAY)
    params={'start_date':DAY.isoformat(),'end_date':DAY.isoformat(),'keyword':'310060'}
    path='/api/subcontract/outbound/external-sync'
    assert setup.client.get(path+'/records',params=params).json()['total']==1
    assert setup.client.get(path+'/runs').status_code==200
    assert 'subcontract_dispatch' in {s['kind'] for s in setup.client.get('/api/admin/external-sync/settings').json()['states']}
    html=setup.client.get('/admin/external-sync?tab=subcontract_dispatch')
    assert html.status_code==200 and 'dsRows' in html.text and 'data-sync-target="subcontract_dispatch"' in html.text
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='worker',role='USER',permissions='{}')
    assert setup.client.get(path+'/records',params=params).status_code==403
    assert setup.client.get(path+'/runs').status_code==403


def test_execute_range_and_automatic_recent_only(setup,monkeypatch):
    calls=[]
    monkeypatch.setattr(client.SubcontractDispatchClient,'__init__',lambda self,*a:None)
    monkeypatch.setattr(client.SubcontractDispatchClient,'fetch_range',lambda self,start,end:(calls.append((start,end)) or [row(start)],[]))
    token,run_id=sync.claim(DAY,DAY,'MANUAL','subcontract_dispatch')
    sync.execute(DAY,DAY,token,run_id,'subcontract_dispatch')
    with setup.sessions() as db:
        assert db.get(Run,run_id).status=='SUCCESS'
        settings=db.get(State,1);settings.enabled=True;settings.initial_completed_at=sync.now();settings.lookback_days=2;settings.next_run_at=None;db.commit()
    sync.schedule_cycle('subcontract_dispatch')
    assert calls==[(DAY,DAY),(DAY-timedelta(days=1),DAY)]
