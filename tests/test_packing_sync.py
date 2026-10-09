"""Packing imports must remain idempotent and independent of native stock."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from test_production_sync import setup, DAY  # shared isolated DB/credentials fixture
from core.security import get_current_user
from models.models import ItemMasterModel
from models.packing import PackingMaster, PackingBox
from models.sales import ShipmentMaster
from models.production_lot import ProductionLotModel
from models.packing_sync import ExternalPackingRecord, PackingSyncState, PackingSyncRun
from models.production_sync import ExternalProductionRecord
from routers.packing_sync import router as sync_router
from routers.packing import router as packing_router
from services import production_sync_service as sync
from services import packing_sync_client as source
from services.production_sync_client import SyncError


def row(part='SOURCE', lot='26100501001', day=DAY, **changes):
    return {'ITEM_NUM': part, 'LOT_NUM': lot, 'CREATE_DATE': day.strftime('%Y%m%d'),
            'LOT_QTY': '240.0000', 'JOB_QTY': '0.0000', 'CONFIRM_DATE': '',
            'PRODUCT_NM': 'Example terminal', 'APPLY_COMP_NM': ''} | changes


def response(rows):
    return {'Common.Set.LotSet.' + k: [','.join(r[k] for r in rows)] for k in row()} | {
        'SO.NET.END': ['SO.NET.END'], 'log.invoke.object': ['MES.Stock.PackList.P_test'],
        'MONITOR.QUERY.START': ['start'], 'MONITOR.QUERY.END': ['end']}


def enable_routes(setup):
    setup.app.include_router(sync_router)
    setup.app.include_router(packing_router)


def test_parser_preserves_cross_part_same_lot_and_source_date():
    rows = [row(), row(part='OTHER', JOB_QTY='80.0000', CONFIRM_DATE='20260326', APPLY_COMP_NM='Customer')]
    parsed, warnings = source.parse_response(response(rows), DAY, DAY)
    assert parsed == rows and not warnings
    assert sum(Decimal(r['LOT_QTY']) for r in parsed) == 480
    assert sum(Decimal(r['JOB_QTY']) for r in parsed) == 80


@pytest.mark.parametrize('field,value', [('CREATE_DATE', 'bad'), ('CONFIRM_DATE', 'bad'),
    ('LOT_QTY', 'NaN'), ('LOT_QTY', '-1'), ('LOT_QTY', ''), ('JOB_QTY', 'bad'), ('ITEM_NUM', ''), ('LOT_NUM', '')])
def test_invalid_packing_data_is_rejected(field, value):
    with pytest.raises(SyncError):
        source.parse_response(response([row(**{field:value})]), DAY, DAY)


def test_counts_redirect_and_wrong_dates_are_rejected():
    data=response([row(),row(part='OTHER')]);data['Common.Set.LotSet.LOT_QTY']=['240']
    with pytest.raises(SyncError, match='건수'): source.parse_response(data,DAY,DAY)
    data=response([row()]);data['Common.Set.LotSet.ROW_COUNT']=['2']
    with pytest.raises(SyncError, match='건수'): source.parse_response(data,DAY,DAY)
    data=response([row()]);data['jump.form.code']=['Login']
    with pytest.raises(SyncError, match='화면 전환'): source.parse_response(data,DAY,DAY)
    with pytest.raises(SyncError, match='기간'): source.parse_response(response([row(day=DAY+timedelta(days=1))]),DAY,DAY)


def test_empty_query_requires_packing_completion_envelope():
    data=response([])
    assert source.parse_response(data,DAY,DAY) == ([],[])
    data['log.invoke.object']=['Common.Login.LoginObj_test']
    with pytest.raises(SyncError): source.parse_response(data,DAY,DAY)


def test_padded_query_uses_packlist_and_only_keeps_requested_day(monkeypatch):
    client=source.PackingClient.__new__(source.PackingClient);client.opener=object();client.context={}
    captured=[]
    def query(_, payload):
        captured.append(payload)
        return response([row(day=DAY-timedelta(days=1)), row(), row(day=DAY+timedelta(days=1))])
    monkeypatch.setattr(source,'query_with_retry',query)
    assert client.fetch_day(DAY) == ([row()],[])
    assert captured[0]['run.object.name']=='MES.Stock.PackList.PackListObj'
    assert captured[0]['MES.Product.Set.JobCondSet.JOB_DATE1']==(DAY-timedelta(days=1)).isoformat()
    assert captured[0]['MES.Product.Set.JobCondSet.JOB_DATE2']==(DAY+timedelta(days=1)).isoformat()


def test_repeat_import_updates_only_snapshot_and_leases_are_independent(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    assert sync.claim(DAY,DAY,'MANUAL','packing') is None
    assert sync.claim(DAY,DAY,'MANUAL') is not None
    rows=[row(),row(part='OTHER')]
    assert sync.save_day(rows,token,'packing')['inserted']==2
    assert sync.save_day(rows,token,'packing')['unchanged']==2
    assert sync.save_day([row(LOT_QTY='241',JOB_QTY='80',CONFIRM_DATE='20260326')],token,'packing')['updated']==1
    with setup.sessions() as db:
        assert db.query(ExternalPackingRecord).count()==2
        updated=db.query(ExternalPackingRecord).filter_by(part_no='SOURCE').one()
        assert updated.packing_qty=='241' and updated.shipment_qty=='80' and updated.shipment_date=='2026-03-26'
        for model in (PackingMaster,PackingBox,ProductionLotModel,ShipmentMaster,ExternalProductionRecord):
            assert db.query(model).count()==0
    with pytest.raises(SyncError,match='여러 건'):sync.save_day([row(),row()],token,'packing')
    with setup.sessions() as db:
        sync.state(db,'packing').lease_token='new-token';db.commit()
    with pytest.raises(SyncError,match='실행 권한'):sync.save_day([row(LOT_QTY='999')],token,'packing')


def test_initial_sync_and_recent_window(setup,monkeypatch):
    captured=[]
    def fake_execute(start,end,token,run_id,kind):
        captured.append((start,end,kind))
        with setup.sessions() as db:
            st=sync.state(db,kind);st.initial_completed_at=sync.now();st.lease_token=None;st.lease_until=None;st.next_run_at=None;db.commit()
    monkeypatch.setattr(sync,'execute',fake_execute)
    with setup.sessions() as db:
        st=sync.state(db,'packing');st.enabled=True;st.lookback_days=3;db.commit()
    sync.schedule_cycle('packing');sync.schedule_cycle('packing')
    assert captured==[(DAY.replace(month=1,day=1),DAY,'packing'),(DAY-timedelta(days=2),DAY,'packing')]


def test_partial_failure_keeps_completed_day_and_retry_is_idempotent(setup,monkeypatch):
    class Client:
        def __init__(self,*args): pass
        def fetch_day(self,day):
            if day>DAY:raise SyncError('Test failure')
            return [row()],[]
    monkeypatch.setattr(source,'PackingClient',Client)
    with setup.sessions() as db:
        sync.state(db,'packing').enabled=True;db.commit()
    token,run_id=sync.claim(DAY,DAY+timedelta(days=1),'INITIAL','packing')
    sync.execute(DAY,DAY+timedelta(days=1),token,run_id,'packing')
    with setup.sessions() as db:
        assert db.query(ExternalPackingRecord).count()==1
        run=db.get(PackingSyncRun,run_id)
        assert run.status=='FAILED' and '2026-03-26' in run.error
        assert json.loads(run.counts_json)['completed_days']==1
        assert sync.state(db,'packing').initial_completed_at is None
        sync.state(db,'packing').next_run_at=None;db.commit()
    token,run_id=sync.claim(DAY,DAY,'INITIAL','packing');sync.execute(DAY,DAY,token,run_id,'packing')
    with setup.sessions() as db:
        assert db.get(PackingSyncRun,run_id).status=='SUCCESS'
        assert sync.state(db,'packing').initial_completed_at
        assert json.loads(db.get(PackingSyncRun,run_id).counts_json)['unchanged']==1
        assert sync.state(db).initial_completed_at is None


def test_all_zero_year_does_not_mark_initial_complete(setup,monkeypatch):
    class Client:
        def __init__(self,*args):pass
        def fetch_day(self,day):return [],[]
    monkeypatch.setattr(source,'PackingClient',Client)
    with setup.sessions() as db:
        sync.state(db,'packing').enabled=True;db.commit()
    start=DAY.replace(month=1,day=1)
    token,run_id=sync.claim(start,DAY,'INITIAL','packing');sync.execute(start,DAY,token,run_id,'packing')
    with setup.sessions() as db:
        assert db.get(PackingSyncRun,run_id).status=='FAILED'
        assert sync.state(db,'packing').initial_completed_at is None


def test_packing_status_mapping_filter_late_registration_and_pagination(setup):
    enable_routes(setup)
    token,_=sync.claim(DAY,DAY,'MANUAL','packing');sync.save_day([row(),row(part='OTHER')],token,'packing')
    items=setup.client.get('/api/packing/status').json()['items']
    assert len(items)==2 and all(r['packing_box_id'] is None and not r['linked'] for r in items)
    with setup.sessions() as db:
        finished=ItemMasterModel(part_no='MES-FINISHED',part_name='MES terminal',account_type='PROD',material_type='FINISHED',is_active='Y',created_at='2026-01-01',updated_at='2026-01-01')
        db.add(finished);db.commit();item_id=finished.id
    assert setup.client.put('/api/production/external-sync/item-maps',json={'source_part_no':'SOURCE','source_process':'포장','item_id':item_id}).status_code==200
    result=setup.client.get('/api/packing/status?part_no=MES-FINISHED').json()['items']
    assert len(result)==1 and result[0]['part_no']=='MES-FINISHED' and result[0]['packing_qty']==240
    assert setup.client.get('/api/packing/status?start_date=2026-03-26').json()['items']==[]
    limited=setup.client.get('/api/packing/status?limit=1').json()
    assert len(limited['items'])==1 and limited['total']==2 and limited['truncated']
    maps=setup.client.get('/api/production/external-sync/item-maps').json()
    assert {r['source_process'] for r in maps}=={'포장'}
    admin_rows=setup.client.get('/api/packing/external-sync/records?keyword=MES-FINISHED').json()
    assert admin_rows['total']==1 and admin_rows['rows'][0]['source_part_no']=='SOURCE'


def test_settings_routes_permissions_and_shared_credential_lock(setup,monkeypatch):
    enable_routes(setup)
    assert setup.client.get('/admin/packing-sync').status_code==200
    assert 'packing_external.js' in setup.client.get('/admin/packing-sync').text
    assert setup.client.put('/api/packing/external-sync/settings',json={'enabled':True,'interval_minutes':60,'lookback_days':7}).status_code==200
    assert setup.client.get('/api/packing/external-sync/settings').json()['enabled']
    assert not setup.client.get('/api/production/external-sync/settings').json()['enabled']
    sync.claim(DAY,DAY,'MANUAL','packing')
    from routers import production_sync as api
    monkeypatch.setattr(api,'validate_login',lambda *_:('test-user','test-pass'))
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'test-user','password':'test-pass'}).status_code==409
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='viewer',role='USER',permissions=None)
    for path in ('/admin/packing-sync','/api/packing/external-sync/settings','/api/packing/external-sync/records','/api/packing/external-sync/runs'):
        assert setup.client.get(path).status_code==403
    assert setup.client.get('/api/packing/status').status_code==200


def add_finished(db, part='SOURCE'):
    item=ItemMasterModel(part_no=part,part_name='Finished',account_type='PROD',material_type='FINISHED',
        is_active='Y',inbound_loc='PACK',created_at='2026-01-01',updated_at='2026-01-01')
    db.add(item);db.flush();return item


def inventory_routes(setup):
    from routers.inventory_lot_location import router
    setup.app.include_router(router)


def test_packing_inventory_partial_full_shipments_repeat_and_updates(setup):
    inventory_routes(setup);enable_routes(setup)
    with setup.sessions() as db:add_finished(db);db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    incoming=[row(lot='PARTIAL',JOB_QTY='80'),row(lot='COMPLETE',JOB_QTY='240')]
    sync.save_day(incoming,token,'packing');sync.save_day(incoming,token,'packing')
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==160
    lots=setup.client.get('/api/inventory/lots?part_no=SOURCE&stock_status=REMAINING').json()['items']
    assert len(lots)==1 and lots[0]['remaining_qty']==160 and lots[0]['used_qty']==80
    assert setup.client.get('/api/inventory/lots?part_no=SOURCE&stock_status=USED').json()['items'][0]['lot_no']=='COMPLETE'
    sync.save_day([row(lot='PARTIAL',JOB_QTY='240')],token,'packing')
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==0
    with setup.sessions() as db:
        assert db.query(ProductionLotModel).count()==0
        assert db.query(PackingMaster).count()==0


def test_native_lot_collision_blocks_external_stock_without_overwriting_native(setup):
    inventory_routes(setup);enable_routes(setup)
    with setup.sessions() as db:
        item=add_finished(db);db.commit();item_id=item.id
    token,_=sync.claim(DAY,DAY,'MANUAL','packing');sync.save_day([row(lot='COLLISION')],token,'packing')
    # Simulate a conflict already stored by older code. New imports must reject it.
    with setup.sessions() as db:
        db.add(ProductionLotModel(item_id=item_id,part_no='SOURCE',lot_no='COLLISION',lot_qty=10,status='ACTIVE'));db.commit()
    with pytest.raises(SyncError,match='내부'):sync.save_day([row(lot='COLLISION')],token,'packing')
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==10
    lots=setup.client.get('/api/inventory/lots?part_no=SOURCE').json()['items']
    assert len(lots)==1 and lots[0]['source']=='생산'
    view=setup.client.get('/api/packing/external-sync/records').json()['rows'][0]
    assert view['stock_status']=='CONFLICT' and view['stock_qty'] is None
    with setup.sessions() as db:assert db.query(ProductionLotModel).one().lot_qty==10


def test_duplicate_external_mapping_excludes_both_and_recovers_after_remapping(setup):
    from models.production_sync import ProductionSyncItemMap
    inventory_routes(setup);enable_routes(setup)
    token,_=sync.claim(DAY,DAY,'MANUAL','packing');sync.save_day([row(part='FIRST'),row(part='SECOND')],token,'packing')
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==0
    with setup.sessions() as db:
        item=add_finished(db,part='MES');other=add_finished(db,part='OTHER');db.flush();other_id=other.id
        db.add_all([ProductionSyncItemMap(source_part_no=p,source_process_name='포장',item_id=item.id) for p in ('FIRST','SECOND')]);db.commit()
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==0
    assert {r['stock_status'] for r in setup.client.get('/api/packing/external-sync/records').json()['rows']}=={'CONFLICT'}
    assert setup.client.put('/api/production/external-sync/item-maps',json={'source_part_no':'SECOND','source_process':'포장','item_id':other_id}).status_code==200
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==480


def test_native_packing_collision_counts_native_box_only(setup):
    inventory_routes(setup);enable_routes(setup)
    with setup.sessions() as db:
        item=add_finished(db);db.commit();item_id=item.id
    token,_=sync.claim(DAY,DAY,'MANUAL','packing');sync.save_day([row(lot='COLLISION')],token,'packing')
    with setup.sessions() as db:
        item=db.get(ItemMasterModel,item_id)
        master=PackingMaster(packing_no='NATIVE',packing_date=DAY.isoformat(),item_id=item.id,part_no=item.part_no,
            box_count=1,box_qty=20,total_qty=20,status='PACKED')
        master.boxes.append(PackingBox(box_no=1,package_lot_no='COLLISION',box_qty=20));db.add(master);db.commit()
    with pytest.raises(SyncError,match='내부'):sync.save_day([row(lot='COLLISION')],token,'packing')
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==20
    lots=setup.client.get('/api/inventory/lots?part_no=SOURCE').json()['items']
    assert len(lots)==1 and lots[0]['source']=='MES 포장'
    assert setup.client.get('/api/packing/external-sync/records').json()['rows'][0]['stock_status']=='CONFLICT'


def test_external_lots_reserve_native_production_packing_and_purchase_numbers(setup):
    from services.lot_service import next_lot_no
    from services.shipping_lot_service import next_shipping_lot_no
    from services.purchase_lot_format import _next_internal_lot
    from test_production_sync import row as production_row
    with setup.sessions() as db:add_finished(db);db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    sync.save_day([row(lot='26032501001'),row(lot='LR260325001')],token,'packing')
    production_token,_=sync.claim(DAY,DAY,'MANUAL')
    sync.save_day([production_row(lot='LX202603250101')],production_token)
    with setup.sessions() as db:
        assert next_shipping_lot_no(db,'SOURCE',DAY.isoformat())=='26032501002'
        add_finished(db,part='OTHER')
        assert next_shipping_lot_no(db,'OTHER',DAY.isoformat())=='26032501001'
        assert next_lot_no(db,'LX',DAY.isoformat(),1)=='LX202603250102'
        assert _next_internal_lot(db,DAY.isoformat(),set())=='LR260325002'


def test_old_packing_dates_are_revisited_even_after_full_shipment(setup,monkeypatch):
    inventory_routes(setup)
    old=DAY-timedelta(days=100)
    with setup.sessions() as db:add_finished(db);db.commit()
    token,_=sync.claim(old,old,'MANUAL','packing');sync.save_day([row(day=old,JOB_QTY='240')],token,'packing')
    with setup.sessions() as db:
        st=sync.state(db,'packing');st.enabled=True;st.lease_token=None;st.lease_until=None;st.next_run_at=None;db.commit()
    visited=[]
    class Client:
        def __init__(self,*args):pass
        def fetch_day(self,day):
            visited.append(day)
            return ([row(day=old,JOB_QTY='80')],[]) if day==old else ([],[])
    monkeypatch.setattr(source,'PackingClient',Client)
    token,run_id=sync.claim(DAY,DAY,'AUTO','packing');sync.execute(DAY,DAY,token,run_id,'packing')
    assert visited==[old,DAY]
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==160


def test_missing_source_lot_excluded_then_recovers_without_duplicate(setup):
    inventory_routes(setup);enable_routes(setup)
    with setup.sessions() as db:add_finished(db);db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    sync.save_day([row()],token,'packing',queried_day=DAY)
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==240
    sync.save_day([],token,'packing',queried_day=DAY)
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==0
    assert setup.client.get('/api/packing/external-sync/records').json()['rows'][0]['stock_status']=='MISSING'
    sync.save_day([row(JOB_QTY='80')],token,'packing',queried_day=DAY)
    assert setup.client.get('/api/inventory/status').json()['stock_qty']==160
    with setup.sessions() as db:assert db.query(ExternalPackingRecord).count()==1


def test_shipment_exceeding_packing_is_rejected():
    with pytest.raises(SyncError,match='초과'):
        source.parse_response(response([row(JOB_QTY='241')]),DAY,DAY)
