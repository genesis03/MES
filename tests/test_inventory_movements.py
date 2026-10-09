"""Unknown storage and packing balances can move without quantity changes."""
from test_production_sync import setup, DAY, add_item
from test_packing_sync import add_finished, row
from routers.inventory_lot_location import router
from models.models import StorageLocationModel
from models.production_lot import ProductionLotModel
from models.packing import PackingMaster, PackingBox
from models.packing_sync import ExternalPackingRecord
from services import production_sync_service as sync


def locations(db):
    db.add(StorageLocationModel(location_code='TARGET', location_name='New location', created_at='2026-01-01'))


def move(client, item, lot):
    return client.post('/api/inventory/movements', json={'item_id':item, 'lot_no':lot,
        'to_location':'TARGET', 'reason':'보관위치 변경'})


def test_native_unspecified_location_and_lot_search(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        item = add_item(db)
        locations(db)
        db.add(ProductionLotModel(item_id=item, part_no='LOCAL-B', lot_no='NATIVE-001', lot_qty=20, storage_location=None))
        db.commit()
    rows=setup.client.get('/api/inventory/movements/lots?keyword=NATIVE-001').json()['items']
    assert len(rows)==1 and rows[0]['storage_location']==''
    response=move(setup.client,item,'NATIVE-001')
    assert response.status_code==200 and response.json()['moved_qty']==20
    assert response.json()['from_location']==''
    rows=setup.client.get('/api/inventory/lots?part_no=LOCAL-B').json()['items']
    assert rows[0]['storage_location']=='New location' and rows[0]['remaining_qty']==20


def test_external_packing_unspecified_same_lot_other_item_and_resync(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        a=add_finished(db); a.inbound_loc=None;aid=a.id
        b=add_finished(db,'OTHER'); b.inbound_loc=None
        locations(db);db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    sync.save_day([row(JOB_QTY='80'),row(part='OTHER')],token,'packing')
    rows=setup.client.get('/api/inventory/movements/lots?keyword=26100501001').json()['items']
    assert len(rows)==2 and all(x['storage_location']=='' for x in rows)
    result=move(setup.client,aid,'26100501001')
    assert result.status_code==200 and result.json()['moved_qty']==160
    rows=setup.client.get('/api/inventory/movements/lots').json()['items']
    assert {x['part_no']:x['storage_location'] for x in rows}=={'SOURCE':'New location','OTHER':''}
    sync.save_day([row(JOB_QTY='100')],token,'packing')
    rows=setup.client.get('/api/inventory/lots?part_no=SOURCE').json()['items']
    assert rows[0]['remaining_qty']==140 and rows[0]['storage_location']=='New location'
    with setup.sessions() as db:
        assert db.query(ExternalPackingRecord).filter_by(part_no='SOURCE').one().packing_qty=='240.0000'
        assert db.query(ProductionLotModel).count()==0
    sync.save_day([row(JOB_QTY='240')],token,'packing')
    assert move(setup.client,aid,'26100501001').status_code==409


def test_native_packing_move_without_recreating_stock(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        item=add_finished(db);item.inbound_loc=None;iid=item.id
        locations(db)
        master=PackingMaster(packing_no='PK1',packing_date='2026-03-25',item_id=iid,part_no='SOURCE',box_count=1,box_qty=30,total_qty=30)
        db.add(master);db.flush();db.add(PackingBox(packing_id=master.id,box_no=1,package_lot_no='BOX1',box_qty=30));db.commit()
    assert move(setup.client,iid,'BOX1').status_code==200
    rows=setup.client.get('/api/inventory/status').json()['items']
    assert rows[0]['storage_location']=='New location' and rows[0]['stock_qty']==30
    with setup.sessions() as db:assert db.query(PackingBox).count()==1
