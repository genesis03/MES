"""Captured protocol, immutable source identities, and native shipping compatibility."""
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_production_sync import setup, ADMIN
from core.security import get_current_user
from models.shipment_sync import ExternalShipment, ExternalShipmentLot, ShipmentSyncState, ShipmentSyncRun
from models.sales import ShipmentMaster, SalesOrderMaster, SalesOrderItem
from models.models import ItemMasterModel, ShippingMasterModel
from services import shipment_sync_client as client
from services import production_sync_service as sync
from services.shipment_sync_client import HEADER, DETAIL, LOT, SyncError
from services.shipment_sync_view import shipment_view
from services.production_sync_mapping import ItemConnections
from services.shipping_analysis_adapter import build_shipping_analysis_rows
from routers import shipping, shipping_inquiry, shipment_sync, external_sync

DAY = date(2026, 10, 23)


def capture():
    return json.loads((Path(__file__).parent/'fixtures/shipment_detail.json').read_text())


def header():
    d = capture()
    return {k: d[HEADER+k][0] for k in ('RULE_NO','OUT_NUM','OUT_DATE','STAT_CODE')} | {'TR_COMP_NM':'(주)유라'}


def record():
    return client.parse_detail(capture(), header())[0]


def listing(headers=None):
    headers = [header()] if headers is None else headers
    d = {HEADER+k: [','.join(h[k] for h in headers)] for k in header()}
    return d | {'SO.NET.END':['SO.NET.END'], 'log.invoke.object':['Common.Pop.OutDirect_test'],
                'MONITOR.QUERY.START':['start'], 'MONITOR.QUERY.END':['end']}


def lease(setup):
    return sync.claim(DAY, DAY, 'MANUAL', 'shipment')


def save(setup, source=None):
    token, run_id = lease(setup)
    sync.save_day([source or record()], token, 'shipment', DAY)
    return token, run_id


def register(setup):
    for router in (shipping.router, shipping_inquiry.router, shipment_sync.router, external_sync.router):
        setup.app.include_router(router)
    for route in shipping.router.routes:
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
            if getattr(dependency.call, "__name__", "") == "permission_dependency":
                setup.app.dependency_overrides[dependency.call] = lambda: ADMIN


def test_captured_detail_links_all_twenty_lots_to_three_lines():
    data, warnings = client.parse_detail(capture(), header())
    assert not warnings
    assert [len(i['lots']) for i in data['items']] == [17, 1, 2]
    assert [sum(float(l['JOB_QTY']) for l in i['lots']) for i in data['items']] == [2040,280,720]
    assert all(i['lot_complete'] for i in data['items'])
    assert data['items'][0]['lots'][0]['LOT_NUM'] == '26101401004'


@pytest.mark.parametrize('field,value',[(LOT+'LOT_TR_NO','wrong'),(LOT+'ITEM_NUM','wrong'),
    (LOT+'TR_NO','wrong'),(LOT+'DET_FIN_ITEM_NO','wrong'),(LOT+'JOB_QTY','999999'),
    (LOT+'LOT_QTY','NaN'),(DETAIL+'RULE_NO','duplicate'),(HEADER+'OUT_NUM','other')])
def test_bad_detail_is_rejected(field,value):
    values = capture()
    original = values[field][0].split(',')
    original[0] = value
    values[field] = [','.join(original)]
    with pytest.raises(SyncError): client.parse_detail(values,header())


def test_missing_lots_are_visible_but_not_printable(setup):
    values = {k:v for k,v in capture().items() if not k.startswith(LOT)}
    data, warnings = client.parse_detail(values,header())
    assert warnings and not any(i['lot_complete'] for i in data['items'])
    save(setup,data)
    with setup.sessions() as db:
        view = shipment_view(db.query(ExternalShipment).one(), ItemConnections(db),True)
        assert view['total_qty']==3040 and not view['printable']
        with pytest.raises(Exception) as error: build_shipping_analysis_rows(db,[-1])
        assert error.value.status_code==409


def test_search_payload_then_load_uses_same_session_key_and_no_write(monkeypatch):
    c = client.ShipmentClient.__new__(client.ShipmentClient)
    c.encrypt_key='939';c.context={};c.opener=object()
    calls=[]
    def query(opener,payload):
        assert opener is c.opener and payload['ENCRYPT.KEY']=='939'
        calls.append(payload)
        return listing() if len(calls)==1 else capture()
    monkeypatch.setattr(client,'query_with_retry',query)
    rows,warnings = c.fetch_day(DAY)
    assert len(rows)==1 and not warnings
    assert [r['jump.form.code'] for r in calls]==['SEARCH','Load']
    assert calls[0][client.COND+'OUT_DATE1']==(DAY-timedelta(days=1)).isoformat()
    assert calls[1][HEADER+'RULE_NO']==header()['RULE_NO']
    assert calls[1]['run.object.name']=='MES.Out.Mana.OutObj'


def test_empty_and_truncated_lists_do_not_silently_remove_data():
    assert client.parse_list(listing([]),DAY,DAY)==[]
    with pytest.raises(SyncError):client.parse_list({'SO.NET.END':['SO.NET.END']},DAY,DAY)
    values=listing();values[HEADER+'ROW_COUNT']=['50']
    with pytest.raises(SyncError):client.parse_list(values,DAY,DAY)
    capped=[header()|{'RULE_NO':str(i)} for i in range(25)]
    with pytest.raises(SyncError,match='페이지'):client.parse_list(listing(capped),DAY,DAY)


def test_save_updates_in_place_replaces_lots_and_marks_disappearance_without_native_changes(setup):
    token,_=save(setup)
    result=sync.save_day([record()],token,'shipment',DAY)
    assert result['unchanged']==1
    changed=record();changed['items'][0]['lots'][0]['LOT_NUM']='CHANGED-LOT'
    assert sync.save_day([changed],token,'shipment',DAY)['updated']==1
    with setup.sessions() as db:
        assert db.query(ExternalShipment).count()==1
        assert db.query(ExternalShipmentLot).count()==20
        assert not db.query(ExternalShipmentLot).filter_by(lot_no='26101401004').count()
        assert db.query(ShipmentMaster).count()==db.query(SalesOrderMaster).count()==db.query(SalesOrderItem).count()==0
        assert db.query(ShippingMasterModel).count()==0
    sync.save_day([],token,'shipment',DAY)
    with setup.sessions() as db:assert not db.query(ExternalShipment).one().present
    sync.save_day([changed],token,'shipment',DAY)
    with setup.sessions() as db:assert db.query(ExternalShipment).one().present


def test_expired_worker_cannot_save_or_mark_disappearance(setup):
    token,_=save(setup)
    with setup.sessions() as db:
        db.get(ShipmentSyncState,1).lease_until=sync.now()-timedelta(seconds=1);db.commit()
    with pytest.raises(SyncError):sync.save_day([],token,'shipment',DAY)
    with setup.sessions() as db:assert db.query(ExternalShipment).one().present


def test_inquiry_detail_readonly_and_existing_label_excel_endpoints(setup):
    register(setup);save(setup)
    with setup.sessions() as db:
        item=ItemMasterModel(part_no='310194-1',part_name='내부 품명',is_active='Y',material_type='FINISHED',account_type='PROD',created_at='2026-10-27',updated_at='2026-10-27')
        db.add(item);db.commit();item_id=item.id
    response=setup.client.get('/api/shipping/inquiry',params={'source':'EXTERNAL','start_date':DAY.isoformat(),'item_id':item_id})
    assert response.status_code==200,response.text
    row=response.json()['items'][0]
    assert row['id']==-1 and row['total_boxes']==20 and row['total_qty']==3040
    assert setup.client.get('/api/shipping/inquiry',params={'source':'INTERNAL'}).json()['items']==[]
    detail=setup.client.get('/api/shipping/inquiry/-1').json()
    assert detail['items'][0]['part_name']=='내부 품명'
    assert detail['items'][0]['boxes'][0]['waiting_lot_no']=='26101401004'
    assert setup.client.delete('/api/shipping/inquiry/-1').status_code==409
    assert setup.client.put('/api/shipping/inquiry/-1',json={'shipment_date':'2026-10-24'}).status_code==409
    available=setup.client.get('/api/shipping/source-shipments').json()['items']
    assert available[0]['id']==-1
    result=setup.client.post('/api/shipping/from-shipments',json={'shipment_ids':[-1]})
    assert result.status_code==200,result.text
    data=result.json()['data']
    assert len(data)==20 and sum(float(r['textBox11']) for r in data)==3040
    assert data[0]['textBox10']=='내부 품명'
    assert data[0]['textBox12']=='26101401004110657'
    assert data[0]['barcode1']=='P310194-1Q120S26101401004110657'
    excel=setup.client.post('/api/shipping/generate-excel-from-shipments',json={'shipment_ids':[-1]})
    assert excel.status_code==200 and excel.content.startswith(b'PK')
    assert setup.client.get('/api/shipping/inquiry?start_date=2027-01-01').json()['items']==[]
    with setup.sessions() as db:
        assert db.query(ShipmentMaster).count()==0
        assert db.query(SalesOrderMaster).count()==0


def test_cancellation_disables_output_and_history_reappears_only_when_source_returns(setup):
    register(setup)
    source=record();source['header']['STAT_CODE']='OUT_CANCEL'
    save(setup,source)
    data=setup.client.get('/api/shipping/inquiry').json()['items']
    assert len(data)==1 and not data[0]['printable']
    assert setup.client.get('/api/shipping/source-shipments').json()['items']==[]
    assert setup.client.post('/api/shipping/from-shipments',json={'shipment_ids':[-1]}).status_code==409


def test_scheduler_and_common_controls_include_shipments(setup,monkeypatch):
    register(setup)
    monkeypatch.setattr(sync,'today',lambda:DAY)
    settings=setup.client.get('/api/admin/external-sync/settings').json()
    assert settings['states'][-1]['kind']=='shipment'
    result=setup.client.put('/api/admin/external-sync/settings',json={
        'enabled':True,'interval_minutes':240,'lookback_days':3,'targets':['shipment']})
    assert result.status_code==200,result.text
    class Client:
        def __init__(self,*args):pass
        def fetch_day(self,day):return ([record()],[]) if day==DAY else ([],[])
    monkeypatch.setattr(client,'ShipmentClient',Client)
    sync.schedule_cycle('shipment')
    with setup.sessions() as db:
        assert db.query(ShipmentSyncRun).one().status=='SUCCESS'
        assert db.query(ExternalShipment).count()==1
    assert setup.client.get('/admin/external-sync?tab=shipment').status_code==200
    assert setup.client.get('/api/shipping/external-sync/runs').json()[0]['status']=='SUCCESS'
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='worker',role='USER',permissions=None)
    assert setup.client.get('/api/shipping/external-sync/runs').status_code==403


def test_shipped_external_lot_numbers_reserved_without_stock_creation(setup):
    from services.external_lot_guard import external_lot_numbers
    save(setup)
    with setup.sessions() as db:assert '26101401004' in external_lot_numbers(db,'261014')


def test_import_keeps_existing_native_order_and_same_number_shipment_independent(setup):
    from models.partner import Partner
    from models.sales import ShipmentItem
    register(setup)
    with setup.sessions() as db:
        partner=Partner(partner_code='CUSTOMER',partner_name='(주)유라',partner_type='CUSTOMER')
        item=ItemMasterModel(part_no='310194-1',part_name='기존 품명',account_type='PROD',
            material_type='FINISHED',is_active='Y',created_at='2026-10-27',updated_at='2026-10-27')
        db.add_all([partner,item]);db.flush()
        order=SalesOrderMaster(order_no='O202610221',order_date=DAY.isoformat(),
            customer_id=partner.id,customer_name=partner.partner_name,status='PARTIAL')
        db.add(order);db.flush()
        order_item=SalesOrderItem(order_id=order.id,item_id=item.id,part_no=item.part_no,
            order_qty=100,shipped_qty=50,status='PARTIAL')
        db.add(order_item);db.flush()
        native=ShipmentMaster(shipment_no='T202610221',shipment_date=DAY.isoformat(),
            sales_order_id=order.id,customer_id=partner.id,customer_name=partner.partner_name)
        db.add(native);db.flush()
        db.add(ShipmentItem(shipment_id=native.id,sales_order_item_id=order_item.id,
            item_id=item.id,part_no=item.part_no,shipped_qty=50))
        db.commit();partner_id=partner.id
    token,_=save(setup)
    result=setup.client.get('/api/shipping/inquiry',params={'customer_id':partner_id}).json()
    assert len(result['items'])==2
    assert {r['total_qty'] for r in result['items']}=={50,3040}
    assert len(set(r['id'] for r in result['items']))==2
    sync.save_day([],token,'shipment',DAY)
    with setup.sessions() as db:
        assert db.query(ShipmentMaster).count()==1
        assert db.query(SalesOrderMaster).one().status=='PARTIAL'
        assert db.query(SalesOrderItem).one().shipped_qty==50
        assert db.query(ShipmentItem).one().shipped_qty==50


def test_active_shipment_import_locks_shared_credentials_and_protocol_key(setup,monkeypatch):
    from routers import production_sync as api
    lease(setup)
    monkeypatch.setattr(api,'validate_login',lambda *_:('test-user','test-pass','initialization'))
    assert setup.client.put('/api/production/external-sync/credentials',json={
        'username':'test-user','password':'test-pass'}).status_code==409
    assert setup.client.put('/api/production/external-sync/protocol-key',json={'encrypt_key':'939'}).status_code==409


def test_external_shipment_item_mapping_uses_shared_mapping_and_current_master_names(setup):
    register(setup);save(setup)
    with setup.sessions() as db:
        item=ItemMasterModel(part_no='MES-FINISHED',part_name='현재 내부 품명',account_type='PROD',
            material_type='FINISHED',is_active='Y',created_at='2026-10-27',updated_at='2026-10-27')
        db.add(item);db.commit();item_id=item.id
    pairs=setup.client.get('/api/production/external-sync/item-maps').json()
    assert any(r['source_part_no']=='310194-1' and r['source_process']=='출고' for r in pairs)
    mapped=setup.client.put('/api/production/external-sync/item-maps',json={
        'source_part_no':'310194-1','source_process':'출고','item_id':item_id})
    assert mapped.status_code==200,mapped.text
    detail=setup.client.get('/api/shipping/inquiry/-1').json()
    assert detail['items'][0]['part_no']=='MES-FINISHED'
    assert detail['items'][0]['source_part_no']=='310194-1'
    result=setup.client.post('/api/shipping/from-shipments',json={'shipment_ids':[-1]})
    assert result.status_code==200,result.text
    assert result.json()['data'][0]['textBox10']=='현재 내부 품명'
    with setup.sessions() as db:
        from services.external_lot_guard import external_lot_numbers
        assert '26101401004' in external_lot_numbers(db,'261014',item_id)


def test_column_mismatch_reports_counts_without_echoing_values():
    values=capture()
    values[DETAIL+'TR_QTY']=['Name,with,commas,EXTRA']
    with pytest.raises(SyncError) as caught:
        client.parse_detail(values,header())
    assert caught.value.diagnostic=={'field':DETAIL+'TR_QTY','expected_count':3,
        'received_count':4,'reason':'column_count_mismatch'}
    assert '3건' in str(caught.value) and '4개' in str(caught.value)
    assert 'EXTRA' not in str(caught.value)


def test_fetch_day_continues_after_bad_document(monkeypatch):
    c=client.ShipmentClient.__new__(client.ShipmentClient)
    c.encrypt_key='939';c.context={};c.opener=object()
    bad_header=header()|{'RULE_NO':'BAD','OUT_NUM':'BAD-DOC'}
    good_header=header()
    def query(_,payload):
        if payload['jump.form.code']=='SEARCH':return listing([bad_header,good_header])
        if payload[HEADER+'RULE_NO']=='BAD':
            values=capture()
            values[HEADER+'RULE_NO']=['BAD'];values[HEADER+'OUT_NUM']=['BAD-DOC']
            values[DETAIL+'TR_QTY']=['A,B,C,D']
            return values
        return capture()
    monkeypatch.setattr(client,'query_with_retry',query)
    rows,_=c.fetch_day(DAY)
    assert len(rows)==1 and rows[0]['header']['RULE_NO']==header()['RULE_NO']
    assert c.query_errors[0]['shipment_no']=='BAD-DOC'
    assert c.query_errors[0]['diagnostic']['received_count']==4


def test_partial_day_keeps_failed_and_unseen_existing_documents(setup):
    token,_=save(setup)
    good=record();good['header']=good['header']|{'RULE_NO':'OTHER','OUT_NUM':'OTHER-DOC'}
    sync.save_day([good],token,'shipment',DAY,complete_day=False)
    with setup.sessions() as db:
        assert db.query(ExternalShipment).count()==2
        assert all(r.present for r in db.query(ExternalShipment))
        assert db.query(ExternalShipmentLot).count()==40


def test_execute_continues_failed_days_and_records_partial_diagnostics(setup,monkeypatch):
    end=DAY+timedelta(days=2)
    with setup.sessions() as db:
        settings=sync.state(db,'shipment');settings.enabled=True;db.commit()
    token,run_id=sync.claim(DAY,end,'INITIAL','shipment')
    calls=[]
    class Client:
        def __init__(self,*_):pass
        def fetch_day(self,day):
            calls.append(day)
            if day==DAY:
                raise SyncError('출고 TR_QTY 개수 불일치',{'field':DETAIL+'TR_QTY',
                    'expected_count':3,'received_count':4,'reason':'column_count_mismatch',
                    'UNSAFE':'must not persist'})
            if day==DAY+timedelta(days=1):
                self.query_errors=[{'date':day.isoformat(),'shipment_no':'BAD-DOC',
                    'error':'일부 전표 오류','diagnostic':{'field':DETAIL+'TR_QTY'}}]
            source=record();source['header']=source['header']|{'OUT_DATE':day.isoformat(),
                'RULE_NO':day.isoformat(),'OUT_NUM':day.isoformat()}
            return [source],[]
    monkeypatch.setattr(client,'ShipmentClient',Client)
    sync.execute(DAY,end,token,run_id,'shipment')
    assert calls==[DAY,DAY+timedelta(days=1),end]
    with setup.sessions() as db:
        run=db.get(ShipmentSyncRun,run_id)
        assert run.status=='PARTIAL'
        counts=json.loads(run.counts_json)
        assert counts['inserted']==2 and counts['failed_queries']==2 and counts['completed_days']==1
        assert counts['errors'][0]['diagnostic']['received_count']==4
        assert 'UNSAFE' not in run.counts_json
        assert db.query(ExternalShipment).count()==2
        assert db.get(ShipmentSyncState,1).initial_completed_at is None
        assert db.get(ShipmentSyncState,1).lease_token is None


@pytest.mark.parametrize('source_names',[None,'Name,with,commas,EXTRA','Name,with,extra,commas,again,extra'])
def test_source_product_names_are_not_parsed_or_required(source_names):
    values=capture()
    if source_names is None:
        values.pop(DETAIL+'PRODUCT_NM')
    else:
        values[DETAIL+'PRODUCT_NM']=[source_names]
    data,warnings=client.parse_detail(values,header())
    assert not warnings
    assert len(data['items'])==3 and sum(len(i['lots']) for i in data['items'])==20
    assert all('PRODUCT_NM' not in item for item in data['items'])


@pytest.mark.parametrize('source_boxes',['16.5','16'])
def test_partial_box_shipments_use_allocated_lots_for_counts_and_output(setup,source_boxes):
    register(setup)
    values=capture()
    values[DETAIL+'PRODUCT_NM']=['TERMINAL, ASSY,OTHER,EXTRA']
    values[DETAIL+'BOX_QTY']=[source_boxes+',1,2']
    quantities=values[LOT+'JOB_QTY'][0].split(',');quantities[0]='60'
    values[LOT+'JOB_QTY']=[','.join(quantities)]
    values[DETAIL+'TR_QTY']=['1980,280,720']
    data,warnings=client.parse_detail(values,header())
    assert not warnings and all(i['lot_complete'] for i in data['items'])
    assert data['items'][0]['BOX_QTY']==source_boxes
    save(setup,data)
    detail=setup.client.get('/api/shipping/inquiry/-1').json()
    assert detail['printable'] and detail['total_boxes']==20 and detail['total_qty']==2980
    assert detail['items'][0]['box_count']==17
    assert detail['items'][0]['part_name']=='' and not detail['items'][0]['linked']
    assert '품번 미연결' in detail['note']
    result=setup.client.post('/api/shipping/from-shipments',json={'shipment_ids':[-1]})
    assert result.status_code==200,result.text
    output=result.json()['data']
    assert len(output)==20 and sum(float(r['textBox11']) for r in output)==2980
    assert output[0]['barcode1']=='P310194-1Q60S26101401004110657'
    with setup.sessions() as db:
        stored=json.loads(db.query(ExternalShipment).one().raw_json)
        assert stored['items'][0]['BOX_QTY']==source_boxes
        assert db.query(ShipmentMaster).count()==db.query(SalesOrderMaster).count()==0


def test_legacy_external_names_are_never_used_as_display_fallback(setup):
    source=record();source['items'][0]['PRODUCT_NM']='외부 품명 사용 금지'
    save(setup,source)
    with setup.sessions() as db:
        view=shipment_view(db.query(ExternalShipment).one(),ItemConnections(db),detail=True)
        assert view['items'][0]['part_name']==''
        assert view['items'][0]['source_part_name']=='외부 품명 사용 금지'
        assert not view['items'][0]['linked']


def test_allocated_quantity_must_still_match_for_fractional_box_output():
    values=capture();values[DETAIL+'BOX_QTY']=['16.5,1,2']
    quantities=values[LOT+'JOB_QTY'][0].split(',');quantities[0]='60'
    values[LOT+'JOB_QTY']=[','.join(quantities)]
    data,warnings=client.parse_detail(values,header())
    assert warnings and not data['items'][0]['lot_complete']


@pytest.mark.parametrize('anchor',[date(2026,10,27),date(2026,12,31)])
@pytest.mark.parametrize('initial',[False,True])
def test_scheduled_shipment_includes_tomorrow_without_shifting_lookback(setup,monkeypatch,anchor,initial):
    monkeypatch.setattr(sync,'today',lambda:anchor)
    with setup.sessions() as db:
        settings=sync.state(db,'shipment');settings.enabled=True;settings.lookback_days=4
        settings.initial_completed_at=None if initial else sync.now();db.commit()
    calls=[]
    monkeypatch.setattr(sync,'claim',lambda start,end,trigger,kind:calls.append((start,end,trigger,kind)))
    sync.schedule_cycle('shipment')
    expected_start=anchor.replace(month=1,day=1) if initial else anchor-timedelta(days=3)
    assert calls==[(expected_start,anchor+timedelta(days=1),'INITIAL' if initial else 'AUTO','shipment')]


def test_other_scheduled_import_still_ends_today(setup,monkeypatch):
    with setup.sessions() as db:
        settings=sync.state(db,'packing');settings.enabled=True
        settings.lookback_days=4;settings.initial_completed_at=sync.now();db.commit()
    calls=[]
    monkeypatch.setattr(sync,'claim',lambda start,end,trigger,kind:calls.append((start,end,kind)))
    sync.schedule_cycle('packing')
    assert calls==[(sync.today()-timedelta(days=3),sync.today(),'packing')]


def test_tomorrow_shipment_can_be_stored_and_queried_with_future_end_date(setup):
    register(setup)
    future=DAY+timedelta(days=1)
    source=record();source['header']['OUT_DATE']=future.isoformat()
    token,_=sync.claim(DAY,future,'MANUAL','shipment')
    sync.save_day([source],token,'shipment',queried_day=future)
    assert setup.client.get('/api/shipping/inquiry',params={'end_date':DAY.isoformat()}).json()['items']==[]
    result=setup.client.get('/api/shipping/inquiry',params={'end_date':future.isoformat()})
    assert result.status_code==200,result.text
    assert result.json()['items'][0]['shipment_date']==future.isoformat()
    result=setup.client.get('/api/shipping/source-shipments',params={'end_date':future.isoformat()})
    assert len(result.json()['items'])==1
