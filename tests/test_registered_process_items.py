"""Searches follow the item master process, regardless of part suffix or BOM links."""
from test_production_sync import setup,add_item
from test_production_month_plan import initialize,value
from core.database import get_db
from models.models import ItemBomModel,ItemMasterModel
from models.production import ProductionWorkOrder
from routers.packing import router as packing_router
from routers.purchase import api_router,get_purchase_db


def test_registered_process_controls_contextual_searches_with_finished_packing_exception(setup,monkeypatch):
    initialize(setup,monkeypatch)
    setup.app.include_router(packing_router);setup.app.include_router(api_router)
    setup.app.dependency_overrides[get_purchase_db]=setup.app.dependency_overrides[get_db]
    with setup.sessions() as db:
        registered={code:add_item(db,part=part,code=code) for code,part in [
            ('LT','REGTEST-LATHE-Ag'),('TP','REGTEST-TAPPING-A'),('PL','REGTEST-PLATING-B'),
            ('ASSY','REGTEST-ASSEMBLY'),('PACK','REGTEST-PACKING-C')]}
        db.get(ItemMasterModel,registered['PACK']).material_type='FINISHED'
        orphan=add_item(db,part='REGTEST-ORPHAN-B',code='')
        inactive=add_item(db,part='REGTEST-INACTIVE-B',code='TP')
        db.get(ItemMasterModel,inactive).is_active='N'
        for code,item_id in registered.items():
            # These misleading BOM links must not add the product to another process.
            db.add(ItemBomModel(parent_item_id=item_id,child_item_id=orphan,
                parent_part_no=db.get(ItemMasterModel,item_id).part_no,child_part_no='REGTEST-ORPHAN-B',
                process_code='TP' if code!='TP' else 'LT',quantity=1,unit='EA',created_at='2026-01-01'))
            db.add(ProductionWorkOrder(work_order_no='REGTEST-'+code,order_date='2026-10-12',item_id=item_id,
                part_no=db.get(ItemMasterModel,item_id).part_no,order_qty=100))
        db.commit()
    for code,item_id in registered.items():
        result=setup.client.get('/api/purchase/items/search',params={'keyword':'REGTEST','process_code':code}).json()
        assert result['total']==1 and [r['item_id'] for r in result['items']]==[item_id]
        for path in ('/api/production/performance/orders','/api/production-run/orders'):
            response=setup.client.get(path,params={'process_code':code})
            assert response.status_code==200,response.text
            rows=[r for r in response.json() if r['work_order_no'].startswith('REGTEST-')]
            assert [r['item_id'] for r in rows]==[item_id]
    plan=setup.client.get('/api/production/month-plan/options').json()
    plan_items={r['id']:r for r in plan['items']}
    assert plan_items[registered['LT']]['process_codes']==['LT']
    assert plan_items[registered['TP']]['process_codes']==['TP']
    assert not {orphan,inactive,registered['PL'],registered['ASSY'],registered['PACK']}&plan_items.keys()
    cycles=setup.client.get('/api/production/cycle-times').json()['items']
    cycle_ids={r['item_id'] for r in cycles}
    assert {registered['LT'],registered['TP']}<=cycle_ids
    assert not {orphan,inactive,registered['PL'],registered['ASSY'],registered['PACK']}&cycle_ids
    packing=setup.client.get('/api/packing/items',params={'q':'REGTEST'}).json()
    assert [r['item_id'] for r in packing]==[registered['PACK']]
    # Generic purchasing still searches all active registered items when no process is selected.
    assert setup.client.get('/api/purchase/items/search',params={'keyword':'REGTEST','process_code':' '}).json()['total']==0
    all_items=setup.client.get('/api/purchase/items/search',params={'keyword':'REGTEST'}).json()
    assert all_items['total']==6
