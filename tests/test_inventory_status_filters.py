from test_production_sync import setup,add_item,DAY
from test_packing_sync import add_finished,row
from models.models import ItemMasterModel,StorageLocationModel
from models.production_lot import ProductionLotModel
from routers.inventory_lot_location import router
from services import production_sync_service as sync


def test_inventory_filters_include_external_packing_and_current_locations(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        semi=add_item(db);db.get(ItemMasterModel,semi).material_type='SEMI'
        raw=add_item(db,'RAW-01');db.get(ItemMasterModel,raw).material_type='RAW'
        finished=add_finished(db,'FIN-01');finished.inbound_loc=None
        db.add(StorageLocationModel(location_code='L1',location_name='Test storage',created_at='2026-01-01'))
        db.add_all([ProductionLotModel(item_id=semi,part_no='LOCAL-B',lot_no='SEMI-1',lot_qty=10,storage_location='L1'),
                    ProductionLotModel(item_id=semi,part_no='LOCAL-B',lot_no='SEMI-2',lot_qty=20,storage_location=None),
                    ProductionLotModel(item_id=raw,part_no='RAW-01',lot_no='RAW-1',lot_qty=30,storage_location='L1')]);db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing');sync.save_day([row(part='FIN-01',JOB_QTY='80')],token,'packing')
    def get(params=''):return setup.client.get('/api/inventory/status'+params).json()
    assert get()['stock_qty']==220
    assert get('?material_type=FINISHED')['stock_qty']==160
    assert get('?material_type=RAW')['stock_qty']==30
    assert get('?material_type=SEMI')['total']==2
    assert get('?storage_location=__UNSPECIFIED__')['stock_qty']==180
    assert get('?material_type=SEMI&storage_location=Test%20storage')['stock_qty']==10
    moved=setup.client.post('/api/inventory/movements',json={'item_id':semi,'lot_no':'SEMI-2','to_location':'L1','reason':'Move'})
    assert moved.status_code==200
    assert get('?material_type=SEMI&storage_location=Test%20storage')['stock_qty']==30
    assert get('?q=RAW&material_type=FINISHED')['total']==0
    assert setup.client.get('/api/inventory/status?material_type=INVALID').status_code==422
