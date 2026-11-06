import copy
import json
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_production_sync import setup, DAY
from core.security import get_current_user
from models.inbound_sync import InboundSyncState,InboundSyncRun,ExternalInboundRecord
from models.models import PurchaseInboundMaster,ItemMasterModel
from models.production_lot import ProductionLotModel
from routers.inbound_sync import router,BASE
from routers.external_sync import router as common_router
from services import production_sync_service as sync
from services import inbound_sync_client as client
from services.production_sync_client import SyncError


def capture():return json.loads((Path(__file__).parent/'fixtures/inbound_confirm_list.json').read_text())


def row(day=DAY,**changes):
    return dict(IN_NUM='I001',HIS_NUM='I001A1',ITEM_NUM='RAW',REQ_COMP_NM='정상 거래처',IN_DATE=day.isoformat(),CONFIRM_DATE=day.isoformat(),CONFIRM_QTY='100',IS_CONFIRM='Y',IN_STATE='IN_PROCESS',PRODUCT_TYPE_NM='원자재',MATERIAL_TYPE_NM='동소재')|changes


def response(rows):
    return {client.PREFIX+k:[','.join(r[k] for r in rows)] for k in row()}|{'SO.NET.END':['SO.NET.END'],'log.invoke.object':['MES.In.InConfirmList_test'],'MONITOR.QUERY.START':['s'],'MONITOR.QUERY.END':['e']}


def test_uploaded_response_excludes_only_test_partner_and_uses_confirm_qty():
    values=capture();values[client.PREFIX+'PRODUCT_NM']=['쉼표,포함,품명은,사용하지,않음']
    rows,notes=client.parse_response(values,date(2026,10,6),date(2026,11,6))
    assert len(rows)==13 and notes==['테스트 거래처 2행 제외']
    assert len([r for r in rows if r['PRODUCT_TYPE_NM']=='원자재'])==11
    assert len([r for r in rows if r['PRODUCT_TYPE_NM']=='반제품'])==2
    assert sum(Decimal(r['CONFIRM_QTY']) for r in rows if r['MATERIAL_TYPE_NM']=='동소재')==7368
    assert all('PRODUCT_NM' not in r for r in rows)
    first_cap=next(r for r in rows if r['ITEM_NUM']=='YR280554')
    assert first_cap['IN_DATE']=='2026-10-08' and first_cap['CONFIRM_DATE']=='2026-10-09'


@pytest.mark.parametrize('partner',['테스트 거래처','테스트거래처','테스트  거래처'])
def test_exclusion_ignores_spaces_not_unrelated_suppliers(partner):
    rows,notes=client.parse_response(response([row(REQ_COMP_NM=partner),row(REQ_COMP_NM='테스트금속',ITEM_NUM='OTHER')]),DAY,DAY)
    assert len(rows)==1 and rows[0]['REQ_COMP_NM']=='테스트금속'


@pytest.mark.parametrize('changes',[{'CONFIRM_QTY':'NaN'},{'CONFIRM_QTY':'1e100'},{'CONFIRM_QTY':'-1'},{'IN_DATE':'2026-02-30'},{'IS_CONFIRM':'N'},{'IN_STATE':'OTHER'},{'HIS_NUM':''},{'REQ_COMP_NM':'거래처,쉼표'},{'IN_DATE':(DAY-timedelta(days=3)).isoformat()}])
def test_invalid_data_rejected(changes):
    with pytest.raises(SyncError):client.parse_response(response([row(**changes)]),DAY,DAY)


def test_empty_page_and_truncated_page_checks():
    empty={'SO.NET.END':['SO.NET.END'],'log.invoke.object':['MES.In.InConfirmList_test'],'MONITOR.QUERY.START':['s'],'MONITOR.QUERY.END':['e']}
    assert client.parse_response(empty,DAY,DAY)==([],[])
    with pytest.raises(SyncError):client.parse_response({'SO.NET.END':['SO.NET.END']},DAY,DAY)
    with pytest.raises(SyncError,match='페이지'):client.parse_response(response([row()]*25),DAY,DAY)
    bad=response([row()]);bad[client.PREFIX+'ROW_COUNT']=['2']
    with pytest.raises(SyncError):client.parse_response(bad,DAY,DAY)


def test_request_uses_current_key_and_keeps_only_requested_day(monkeypatch):
    instance=client.InboundClient.__new__(client.InboundClient);instance.context={};instance.opener=object()
    captured=[]
    monkeypatch.setattr(client,'client_common',lambda _: {'ENCRYPT.KEY':'live-key'})
    def query(opener,payload):
        captured.append(payload)
        return response([row(DAY-timedelta(days=1)),row(),row(DAY+timedelta(days=1))])
    monkeypatch.setattr(client,'query_with_retry',query)
    rows,_=instance.fetch_day(DAY)
    assert len(rows)==1 and rows[0]['IN_DATE']==DAY.isoformat()
    assert captured[0]['ENCRYPT.KEY']=='live-key'
    assert captured[0]['run.object.name']=='MES.In.InConfirmList.InConfirmListObj'
    assert captured[0][client.COND+'IN_DATE1']==(DAY-timedelta(days=1)).isoformat()
    assert captured[0][client.COND+'IN_DATE2']==(DAY+timedelta(days=1)).isoformat()


def test_storage_is_idempotent_tracks_missing_restores_and_does_not_change_stock(setup):
    setup.app.include_router(router)
    token,_=sync.claim(DAY,DAY,'MANUAL','inbound')
    rows=[row(),row(ITEM_NUM='OTHER',CONFIRM_QTY='200'),row(REQ_COMP_NM='테스트 거래처',ITEM_NUM='TEST')]
    assert sync.save_day(rows,token,'inbound',queried_day=DAY)['inserted']==2
    assert sync.save_day(rows,token,'inbound',queried_day=DAY)['unchanged']==2
    assert sync.save_day([row(CONFIRM_QTY='90')],token,'inbound',queried_day=DAY)['updated']==1
    with setup.sessions() as db:
        assert db.query(ExternalInboundRecord).count()==2
        assert db.query(ExternalInboundRecord).filter_by(present=True).count()==1
        assert db.query(PurchaseInboundMaster).count()==0 and db.query(ProductionLotModel).count()==0
    assert sync.save_day(rows,token,'inbound',queried_day=DAY)['updated']==2
    with pytest.raises(SyncError):sync.save_day(rows,token,'inbound',queried_day=DAY+timedelta(days=1))
    with setup.sessions() as db:
        db.get(InboundSyncState,1).lease_token='changed';db.commit()
    with pytest.raises(SyncError):sync.save_day([],token,'inbound',queried_day=DAY)


def test_same_history_different_parts_and_repeated_same_part_are_not_lost(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL','inbound')
    counts=sync.save_day([row(),row(),row(ITEM_NUM='SECOND')],token,'inbound',queried_day=DAY)
    assert counts['received']==2 and counts['inserted']==2
    with setup.sessions() as db:
        assert db.query(ExternalInboundRecord).filter_by(part_no='RAW').one().confirmed_qty=='200'
    with pytest.raises(SyncError):sync.save_day([row(),row(REQ_COMP_NM='다른 거래처')],token,'inbound',queried_day=DAY)
    with setup.sessions() as db:assert db.query(ExternalInboundRecord).filter_by(part_no='RAW').one().confirmed_qty=='200'


def test_records_search_unit_unknown_and_admin_permissions(setup):
    setup.app.include_router(router)
    token,_=sync.claim(DAY,DAY,'MANUAL','inbound');sync.save_day([row()],token,'inbound',queried_day=DAY)
    params={'start_date':DAY.isoformat(),'end_date':DAY.isoformat(),'keyword':'RA'}
    r=setup.client.get(BASE+'/records',params=params)
    assert r.status_code==200 and r.json()['rows'][0]['unit']==''
    assert r.json()['rows'][0]['part_name']==''
    assert setup.client.get(BASE+'/records',params=params|{'product_type':'반제품'}).json()['total']==0
    assert setup.client.get(BASE+'/runs').status_code==200
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='worker',role='USER',permissions='{}')
    assert setup.client.get(BASE+'/records',params=params).status_code==403
    assert setup.client.get(BASE+'/runs').status_code==403


def test_execute_and_schedule_are_range_limited_and_in_common_controls(setup,monkeypatch):
    visited=[]
    monkeypatch.setattr(client.InboundClient,'__init__',lambda self,*a:None)
    monkeypatch.setattr(client.InboundClient,'fetch_day',lambda self,day:(visited.append(day) or [row(day)],[]))
    old=DAY-timedelta(days=90)
    token,_=sync.claim(old,old,'MANUAL','inbound');sync.save_day([row(old,HIS_NUM='OLD')],token,'inbound',queried_day=old)
    with setup.sessions() as db:
        state=db.get(InboundSyncState,1);state.enabled=True;state.initial_completed_at=sync.now();state.lookback_days=1;state.lease_token=None;state.lease_until=None;state.next_run_at=None;db.commit()
    sync.schedule_cycle('inbound')
    assert visited==[DAY]
    with setup.sessions() as db:
        run=db.query(InboundSyncRun).order_by(InboundSyncRun.id.desc()).first()
        assert run.status=='SUCCESS' and json.loads(run.counts_json)['received']==1
    setup.app.include_router(common_router)
    states=setup.client.get('/api/admin/external-sync/settings').json()['states']
    assert next(s for s in states if s['kind']=='inbound')['name']=='입고현황'
    page=setup.client.get('/admin/external-sync?tab=inbound')
    assert page.status_code==200 and 'inRows' in page.text and 'data-sync-target="inbound"' in page.text


def test_inbound_lease_blocks_shared_protocol_key_change(setup):
    sync.claim(DAY,DAY,'MANUAL','inbound')
    response=setup.client.put('/api/production/external-sync/protocol-key',json={'encrypt_key':'1944'})
    assert response.status_code==409

def test_parser_failure_does_not_hide_previously_saved_day(setup,monkeypatch):
    token,_=sync.claim(DAY,DAY,'MANUAL','inbound')
    sync.save_day([row()],token,'inbound',queried_day=DAY)
    with setup.sessions() as db:
        state=db.get(InboundSyncState,1);state.lease_token=None;state.lease_until=None;db.commit()
    monkeypatch.setattr(client.InboundClient,'__init__',lambda self,*a:None)
    def fail(self,day):raise SyncError('invalid response')
    monkeypatch.setattr(client.InboundClient,'fetch_day',fail)
    token,run_id=sync.claim(DAY,DAY,'MANUAL','inbound');sync.execute(DAY,DAY,token,run_id,'inbound')
    with setup.sessions() as db:
        assert db.get(InboundSyncRun,run_id).status=='FAILED'
        assert db.query(ExternalInboundRecord).one().present is True
