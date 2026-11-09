from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import json
import pytest
from test_production_sync import setup,DAY
from test_subcontract_dispatch_sync import row
from models.models import ItemMasterModel,ItemBomModel,ProcessModel,StorageLocationModel
from models.partner import Partner
from models.production_lot import ProductionLotModel
from models.lot_relation import LotRelationModel
from models.subcontract import SubcontractLotAllocation,SubcontractOrderMaster
from models.subcontract_outbound import SubcontractOutboundMaster,SubcontractOutboundLot
from models.subcontract_inbound import SubcontractInboundMaster
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record,ExternalSubcontractReceiptSource as Link
from routers import subcontract_inbound,subcontract_inbound_lot_policy,subcontract_outbound,subcontract_inbound_edit
from services import production_sync_service as sync
from services.subcontract_dispatch_store import save_range

BASE='/api/subcontract/inbound'


def masters(setup):
    setup.app.include_router(subcontract_inbound_lot_policy.router)
    setup.app.include_router(subcontract_inbound.router)
    setup.app.include_router(subcontract_outbound.router)
    setup.app.include_router(subcontract_inbound_edit.router)
    with setup.sessions() as db:
        previous=ItemMasterModel(part_no='310060-B',part_name='가공 전',account_type='반제품',material_type='동소재',unit='EA',created_at='now')
        target=ItemMasterModel(part_no='310060-Ag',part_name='은도금 후',account_type='반제품',material_type='동소재',unit='EA',created_at='now')
        db.add_all([previous,target,Partner(partner_code='V1',partner_name='(주)혜성금속',partner_type='VENDOR',is_active='Y'),ProcessModel(process_code='PL',process_name='은도금',created_at='now'),StorageLocationModel(location_code='S-OUT',location_name='외주가공 저장위치',created_at='now'),StorageLocationModel(location_code='S-AG',location_name='은도금 저장위치',created_at='now')]);db.flush()
        db.add(ItemBomModel(parent_item_id=target.id,child_item_id=previous.id,parent_part_no=target.part_no,child_part_no=previous.part_no,process_code='PL',unit='EA',created_at='now'));db.commit()


def seed(setup,rows=None,start=DAY,end=DAY):
    token,_=sync.claim(start,end,'MANUAL','subcontract_dispatch')
    save_range(rows or [row()],token,start,end)
    with setup.sessions() as db:return db.query(Record).order_by(Record.id).first().id


def prepare(setup,record_id):
    response=setup.client.post(f'{BASE}/external/{record_id}/prepare')
    assert response.status_code==200,response.text
    return response.json()


def payload(source,qty,lot_index=0):
    lot=source['items'][0]['lots'][lot_index]
    return dict(outbound_id=source['outbound_id'],inbound_date=DAY.isoformat(),storage_location='S-AG',lot_results=[dict(outbound_lot_id=lot['outbound_lot_id'],inbound_qty=qty,sample_qty=0,supplier_lot_no='SUPPLIER-LOT')])


def test_select_external_dispatch_then_partial_and_full_manual_receipts(setup):
    masters(setup);record_id=seed(setup)
    listing=setup.client.get(BASE+'/outbounds').json()['items']
    assert len(listing)==1 and listing[0]['outbound_id']==-record_id
    with setup.sessions() as db:assert db.query(SubcontractOutboundMaster).count()==0
    source=prepare(setup,record_id)
    assert source['items'][0]['part_no']=='310060-Ag' and source['total_remaining_qty']==100
    assert source['items'][0]['lots'][0]['source_lot_no']=='LS001'
    assert prepare(setup,record_id)['outbound_id']==source['outbound_id']
    assert setup.client.get('/api/subcontract/outbound/orders').json()['items']==[]
    assert setup.client.get(f"/api/subcontract/outbound/order/{source['order_id']}").status_code==409
    with setup.sessions() as db:
        assert db.query(Link).count()==1 and db.query(SubcontractOutboundMaster).count()==1
        assert db.query(SubcontractLotAllocation).count()==0
        assert db.query(ProductionLotModel).count()==0 and db.query(SubcontractInboundMaster).count()==0
    first=setup.client.post(BASE,json=payload(source,40));assert first.status_code==200,first.text
    first_lot=first.json()['items'][0]['lots'][0]['child_lot_no'];assert first_lot.startswith('LZ') and first_lot!='LS001'
    updated=setup.client.get(f"{BASE}/outbound/{source['outbound_id']}").json()
    assert updated['total_received_qty']==40 and updated['total_remaining_qty']==60
    assert setup.client.post(BASE,json=payload(source,61)).status_code==422
    second=setup.client.post(BASE,json=payload(source,60));assert second.status_code==200,second.text
    assert second.json()['items'][0]['lots'][0]['child_lot_no']!=first_lot
    assert setup.client.post(BASE,json=payload(source,1)).status_code==409
    listing=setup.client.get(BASE+'/outbounds').json()['items'];assert len(listing)==1 and listing[0]['remaining_qty']==0
    history=setup.client.get('/api/subcontract/outbound/history').json()['items'];assert len(history)==1 and history[0]['source']=='EXTERNAL'
    assert setup.client.post(f"/api/subcontract/outbound/{source['outbound_id']}/cancel").status_code==409
    with setup.sessions() as db:
        assert sum(l.lot_qty for l in db.query(ProductionLotModel))==100
        assert db.query(LotRelationModel).filter_by(parent_lot_no='LS001').count()==2


def test_first_full_receipt_also_preserves_external_source_and_generates_local_lot(setup):
    masters(setup);source=prepare(setup,seed(setup))
    received=setup.client.post(BASE,json=payload(source,100));assert received.status_code==200,received.text
    assert received.json()['items'][0]['lots'][0]['child_lot_no'].startswith('LZ')
    with setup.sessions() as db:assert db.query(ProductionLotModel).filter_by(lot_no='LS001').count()==0


@pytest.mark.parametrize('status,qty',[('REQ_IN',''),('REQ_FIN','100'),('REQ_OUT','1')])
def test_previously_received_external_records_cannot_be_received_again(setup,status,qty):
    masters(setup);record_id=seed(setup,[row(STAT_CODE=status,ETC_IN_QTY=qty)])
    assert setup.client.get(BASE+'/outbounds').json()['items']==[]
    assert setup.client.post(f'{BASE}/external/{record_id}/prepare').status_code==409
    with setup.sessions() as db:assert db.query(Link).count()==0


@pytest.mark.parametrize('field,value',[('outbound_qty','99'),('lot_no','CHANGED'),('source_status','REQ_FIN')])
def test_changed_source_blocks_receipt_without_stock_updates(setup,field,value):
    masters(setup);record_id=seed(setup);source=prepare(setup,record_id)
    with setup.sessions() as db:setattr(db.get(Record,record_id),field,value);db.commit()
    response=setup.client.post(BASE,json=payload(source,40));assert response.status_code==409,response.text
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0 and db.query(SubcontractInboundMaster).count()==0


@pytest.mark.parametrize('missing',['partner','bom','process','storage'])
def test_missing_master_mapping_reports_actionable_error_without_reference_or_stock(setup,missing):
    masters(setup);record_id=seed(setup)
    model={'partner':Partner,'bom':ItemBomModel,'process':ProcessModel,'storage':StorageLocationModel}[missing]
    with setup.sessions() as db:db.query(model).delete();db.commit()
    response=setup.client.post(f'{BASE}/external/{record_id}/prepare');assert response.status_code==422,response.text
    with setup.sessions() as db:assert db.query(Link).count()==0 and db.query(SubcontractOutboundMaster).count()==0


def test_unselected_older_external_dispatch_still_blocks_fifo(setup):
    masters(setup)
    earlier=DAY-timedelta(days=1)
    seed(setup,[row(earlier,REQ_NUM='R000',RULE_NO='old',LOT_NUM='LS-OLD'),row(REQ_NUM='R001',RULE_NO='new',LOT_NUM='LS-NEW')],start=earlier)
    with setup.sessions() as db:record_id=db.query(Record).filter_by(source_key='new').one().id
    source=prepare(setup,record_id)
    result=setup.client.post(BASE,json=payload(source,50));assert result.status_code==409 and 'LS-OLD' in result.text
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0


def test_cannot_skip_lot_in_same_document_and_concurrent_over_receipt(setup):
    masters(setup);record_id=seed(setup,[row(),row(RULE_NO='line2',LOT_NUM='LS002')]);source=prepare(setup,record_id)
    assert setup.client.post(BASE,json=payload(source,50,lot_index=1)).status_code==409
    with ThreadPoolExecutor(2) as pool:responses=list(pool.map(lambda _:setup.client.post(BASE,json=payload(source,60)),range(2)))
    assert sorted(r.status_code for r in responses)==[200,422]
    with setup.sessions() as db:assert sum(l.lot_qty for l in db.query(ProductionLotModel))==60


def test_existing_stock_lot_is_not_reissued_or_overwritten(setup):
    masters(setup);record_id=seed(setup)
    with setup.sessions() as db:
        target=db.query(ItemMasterModel).filter_by(part_no='310060-Ag').one()
        db.add(ProductionLotModel(lot_no='LS001',item_id=target.id,part_no=target.part_no,lot_qty=5,status='ACTIVE',storage_location='S-AG'));db.commit()
    result=setup.client.post(f'{BASE}/external/{record_id}/prepare');assert result.status_code==409
    with setup.sessions() as db:assert db.query(ProductionLotModel).one().lot_qty==5 and db.query(Link).count()==0


def test_cancel_receipt_restores_remaining_and_can_receive_again(setup):
    masters(setup);source=prepare(setup,seed(setup))
    received=setup.client.post(BASE,json=payload(source,100));assert received.status_code==200,received.text
    response=setup.client.post(f"{BASE}/{received.json()['id']}/cancel");assert response.status_code==200,response.text
    result=setup.client.get(f"{BASE}/outbound/{source['outbound_id']}").json();assert result['total_remaining_qty']==100
    received=setup.client.post(BASE,json=payload(source,100));assert received.status_code==200,received.text
    with setup.sessions() as db:assert sum(l.lot_qty for l in db.query(ProductionLotModel).filter_by(status='ACTIVE'))==100


def test_concurrent_selection_creates_only_one_reference(setup):
    masters(setup);record_id=seed(setup)
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda _:setup.client.post(f'{BASE}/external/{record_id}/prepare'),range(2)))
    assert [r.status_code for r in responses]==[200,200],[r.text for r in responses]
    assert responses[0].json()['outbound_id']==responses[1].json()['outbound_id']
    with setup.sessions() as db:
        assert db.query(Link).count()==1 and db.query(SubcontractOrderMaster).count()==1


def test_same_quantity_format_change_does_not_break_receipt(setup):
    masters(setup);record_id=seed(setup);source=prepare(setup,record_id)
    with setup.sessions() as db:db.get(Record,record_id).outbound_qty='100.000';db.commit()
    response=setup.client.post(BASE,json=payload(source,100));assert response.status_code==200,response.text


def test_tapping_external_receipt_uses_tapping_bom_and_lc_lot(setup):
    masters(setup)
    with setup.sessions() as db:
        db.add(ProcessModel(process_code='TP',process_name='탭핑',created_at='now'))
        db.query(ItemBomModel).one().process_code='TP'
        target=db.query(ItemMasterModel).filter_by(part_no='310060-Ag').one();target.part_no='310060-TP';target.part_name='탭핑 후';db.commit()
    record_id=seed(setup,[row(ETC_REQ_TYPE='PROC_TAB',ETC_REQ_TYPE_NM='탭핑가공')]);source=prepare(setup,record_id)
    assert source['items'][0]['part_no']=='310060-TP'
    result=setup.client.post(BASE,json=payload(source,100));assert result.status_code==200,result.text
    assert result.json()['items'][0]['lots'][0]['child_lot_no'].startswith('LC')
