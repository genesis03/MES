from datetime import datetime
from test_inventory_ledger import env,external_meta
from models.packing_sync import ExternalPackingRecord,PackingSourcePresence
from models.shipment_sync import ExternalShipment,ExternalShipmentLot
from models.packing import PackingMaster,PackingBox
from routers.inventory_lot_trace import router as search_router
from routers.inventory_lot_trace_tree import router as tree_router


def seed(env):
    env.client.app.include_router(search_router);env.client.app.include_router(tree_router)
    record=ExternalPackingRecord(source_key='P',part_no='310186-1',lot_no='26082701004',packing_date='2026-08-27',packing_qty='120',shipment_qty='120',shipment_date='2026-09-01',customer_name='고객',raw_json='{}',**external_meta())
    shipment=ExternalShipment(source_key='S',shipment_no='T1',shipment_date='2026-09-01',status='OUT_FIN',present=True,raw_json='{}',**external_meta())
    env.db.add_all([record,shipment]);env.db.flush()
    env.db.add(ExternalShipmentLot(shipment_id=shipment.id,detail_key='D',source_lot_key='L',part_no='310186-1',lot_no='26082701004',allocated_qty='120'));env.db.commit()
    return record,shipment


def test_partial_and_exact_external_outbound_search(env):
    seed(env)
    for keyword in ['260827','26082701004']:
        response=env.client.get('/api/inventory/lot-trace/search',params={'search_type':'OUTBOUND','q':keyword,'part_no':'310186-1'})
        assert response.status_code==200,response.text
        rows=response.json()['items'];assert len(rows)==1
        assert rows[0]['lot_no']=='26082701004' and rows[0]['part_no']=='310186-1'
        assert rows[0]['source']=='EXTERNAL' and rows[0]['qty']==120
    trace=env.client.get('/api/inventory/lot-trace/tree',params={'lot_no':'26082701004','part_no':'310186-1','source':'EXTERNAL'}).json()
    assert {row['process'] for row in trace['rows']}=={'외부 포장','외부 출고'}
    assert all(not row['child_lot_no'] for row in trace['rows'])
    assert trace['notes']


def test_same_lot_other_part_and_native_source_are_not_collapsed(env):
    seed(env)
    env.db.add(ExternalPackingRecord(source_key='P2',part_no='OTHER',lot_no='26082701004',packing_date='2026-08-27',packing_qty='80',shipment_qty='0',shipment_date='',customer_name='',raw_json='{}',**external_meta()))
    pack=PackingMaster(packing_no='P1',packing_date='2026-08-27',part_no='310186-1',item_id=3,box_count=1,box_qty=120,total_qty=120)
    pack.boxes=[PackingBox(box_no=1,package_lot_no='26082701004',box_qty=120)];env.db.add(pack);env.db.commit()
    rows=env.client.get('/api/inventory/lot-trace/search?search_type=OUTBOUND&q=26082701004').json()['items']
    assert len(rows)==3
    trace=env.client.get('/api/inventory/lot-trace/tree?lot_no=26082701004&part_no=OTHER&source=EXTERNAL').json()
    assert {row['part_no'] for row in trace['rows']}=={'OTHER'}


def test_missing_source_records_are_excluded(env):
    record,shipment=seed(env)
    env.db.add(PackingSourcePresence(record_id=record.id,present=False,checked_at=datetime.now()));shipment.present=False;env.db.commit()
    assert env.client.get('/api/inventory/lot-trace/search?search_type=OUTBOUND&q=260827').json()['items']==[]
