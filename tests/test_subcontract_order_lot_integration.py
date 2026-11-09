import json
from datetime import date
from decimal import Decimal
from test_production_sync import setup,DAY
from test_subcontract_order_lots import rows,capture
from test_subcontract_external_receipt import masters,seed,prepare,payload,BASE
from test_subcontract_dispatch_sync import row
from models.models import ItemMasterModel,ItemBomModel
from models.production_lot import ProductionLotModel
from models.production_sync import ExternalProductionRecord
from models.subcontract_inbound import SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundMaster
from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record,ExternalSubcontractReceiptSource as Link
from routers.subcontract_dispatch_sync import router as sync_router
from services import production_sync_service as sync
from services.subcontract_order_lots import parse_detail
from services.subcontract_dispatch_store import save_range


def test_captured_66_lots_persist_and_reach_history_and_manual_receipt(setup):
    masters(setup);setup.app.include_router(sync_router)
    actual=parse_detail(capture(),rows())
    with setup.sessions() as db:
        for record in actual:
            previous=ItemMasterModel(part_no=record['ITEM_NUM'],part_name='이전 품목',account_type='반제품',material_type='동소재',unit='EA',created_at='now')
            target=ItemMasterModel(part_no=record['ITEM_NUM']+'-RESULT',part_name='은도금 후',account_type='반제품',material_type='동소재',unit='EA',created_at='now')
            db.add_all([previous,target]);db.flush()
            db.add(ItemBomModel(parent_item_id=target.id,child_item_id=previous.id,parent_part_no=target.part_no,child_part_no=previous.part_no,process_code='PL',unit='EA',created_at='now'))
            for child in record['_ORDER_LOTS']:
                db.add(ProductionLotModel(lot_no=child['LOT_NUM'],item_id=previous.id,part_no=previous.part_no,lot_qty=float(child['LOT_QTY']),storage_location='S-OUT',status='ACTIVE'))
        db.commit()
    day=date(2026,11,7);token,_=sync.claim(day,day,'MANUAL','subcontract_dispatch')
    assert save_range(actual,token,day,day)['inserted']==4
    assert save_range(actual,token,day,day)['unchanged']==4
    with setup.sessions() as db:
        assert db.query(ProductionLotModel).count()==66 and db.query(SubcontractInboundMaster).count()==0
        record_id=db.query(Record).order_by(Record.id).first().id
    data=setup.client.get(f'/api/subcontract/outbound/{-record_id}').json()
    assert sum(item['lot_count'] for item in data['items'])==66
    assert sum(Decimal(lot['outbound_qty']) for item in data['items'] for lot in item['lots'])==10340
    assert all(not lot['lot_no'].startswith('LS') for item in data['items'] for lot in item['lots'])
    source=prepare(setup,record_id)
    assert sum(len(item['lots']) for item in source['items'])==66 and source['total_allocated_qty']==10340
    original_qty={lot['source_lot_no']:lot['allocated_qty'] for item in source['items'] for lot in item['lots']}
    for parent in actual:
        for child in parent['_ORDER_LOTS']:assert Decimal(str(original_qty[child['LOT_NUM']]))==Decimal(child['JOB_QTY'])
    first=source['items'][0]['lots'][0]
    body=payload(source,first['remaining_qty'])
    received=setup.client.post(BASE,json=body);assert received.status_code==200,received.text
    assert received.json()['items'][0]['lots'][0]['source_lot_no']==first['source_lot_no']
    assert received.json()['items'][0]['lots'][0]['child_lot_no'].startswith('LZ')
    with setup.sessions() as db:
        assert db.query(ProductionLotModel).count()==67 and db.query(SubcontractInboundMaster).count()==1
    response=setup.client.get('/api/subcontract/outbound/external-sync/records',params={'start_date':day.isoformat(),'end_date':day.isoformat(),'keyword':first['source_lot_no']})
    assert response.status_code==200 and response.json()['total']==1
    assert response.json()['rows'][0]['lot_count']>0


def change_to_actual_children(setup,record_id,source,received=False):
    with setup.sessions() as db:
        record=db.get(Record,record_id);raw=json.loads(record.raw_json)
        raw['_ORDER_LOTS']=[dict(RULE_NO='original-1',LOT_NUM='LX-ORIGINAL-1',ITEM_NUM=record.previous_part_no,ITEM_NO='previous',JOB_QTY='40',LOT_QTY='45',REST_QTY='5'),dict(RULE_NO='original-2',LOT_NUM='LX-ORIGINAL-2',ITEM_NUM=record.previous_part_no,ITEM_NO='previous',JOB_QTY='60',LOT_QTY='60',REST_QTY='0')]
        record.raw_json=json.dumps(raw)
        link=db.query(Link).filter_by(outbound_id=source['outbound_id']).one();before=json.loads(link.snapshot_json)
        for item in before:item.pop('order_lots',None)
        link.snapshot_json=json.dumps(before,ensure_ascii=False,sort_keys=True);db.commit()


def test_previously_selected_aggregate_can_be_relinked_if_no_receipts_exist(setup):
    masters(setup);record_id=seed(setup);old=prepare(setup,record_id)
    change_to_actual_children(setup,record_id,old)
    listing=setup.client.get(BASE+'/outbounds').json()['items'];assert listing[0]['outbound_id']==-record_id
    fresh=prepare(setup,record_id)
    assert fresh['outbound_id']==old['outbound_id']
    assert [lot['source_lot_no'] for lot in fresh['items'][0]['lots']]==['LX-ORIGINAL-1','LX-ORIGINAL-2']
    with setup.sessions() as db:assert db.query(SubcontractOutboundMaster).count()==1 and db.query(SubcontractInboundMaster).count()==0


def test_existing_aggregate_receipt_is_preserved_and_not_silently_reassigned(setup):
    masters(setup);record_id=seed(setup);old=prepare(setup,record_id)
    received=setup.client.post(BASE,json=payload(old,40));assert received.status_code==200
    change_to_actual_children(setup,record_id,old)
    blocked=setup.client.post(f'{BASE}/external/{record_id}/prepare');assert blocked.status_code==409 and '입고 이력' in blocked.text
    historical=setup.client.get(f"{BASE}/{received.json()['id']}").json()
    assert historical['items'][0]['lots'][0]['source_lot_no']=='LS001'
    with setup.sessions() as db:assert db.query(ProductionLotModel).one().lot_qty==40


def test_aggregate_without_verified_lots_cannot_start_manual_receipt(setup):
    masters(setup);legacy=row();legacy.pop('_ORDER_LOTS');record_id=seed(setup,[legacy])
    blocked=setup.client.post(f'{BASE}/external/{record_id}/prepare');assert blocked.status_code==409 and '다시 가져오세요' in blocked.text
    with setup.sessions() as db:assert db.query(Link).count()==0


def test_original_lot_from_production_sync_is_a_parent_not_duplicate_receipt(setup):
    masters(setup);source=row();source['_ORDER_LOTS'][0]['LOT_NUM']='LX202603240101'
    record_id=seed(setup,[source])
    with setup.sessions() as db:
        instant=sync.now()
        db.add(ExternalProductionRecord(source_key='original-production',lot_no='LX202603240101',job_no='JOB-ORIGINAL',part_no='310060-B',process_name='복합선반',work_date=DAY.isoformat(),job_qty='100',lot_qty='100',fault_qty='0',raw_json='{}',content_hash='original',first_seen_at=instant,last_seen_at=instant,changed_at=instant));db.commit()
    linked=prepare(setup,record_id)
    assert linked['items'][0]['lots'][0]['source_lot_no']=='LX202603240101'
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0
    received=setup.client.post(BASE,json=payload(linked,100));assert received.status_code==200,received.text
    with setup.sessions() as db:
        assert db.query(ProductionLotModel).count()==1 and db.query(ExternalProductionRecord).one().lot_qty=='100'
