from test_production_sync import setup,add_item
from test_packing_sync import add_finished
from models.models import ItemMasterModel
from models.packing import PackingMaster
from routers.packing import router


def test_all_finished_items_are_searchable_without_process_or_result_cap(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        for i in range(65):
            item=add_finished(db,f'PACK-SEARCH-{i:03}')
            item.production_loc=['LT','TP','PL','ASSY','PACK',None][i%6]
        semi=add_item(db,part='PACK-SEARCH-SEMI',code='PACK')
        raw=add_item(db,part='PACK-SEARCH-RAW',code='PACK')
        db.get(ItemMasterModel,raw).material_type='RAW'
        inactive=add_finished(db,'PACK-SEARCH-INACTIVE');inactive.is_active='N'
        db.commit()
    # No process master is required, and finished items in any/no registered process are included.
    response=setup.client.get('/api/packing/items',params={'q':'PACK-SEARCH'})
    assert response.status_code==200,response.text
    rows=response.json()
    assert len(rows)==65 and [r['part_no'] for r in rows]==[f'PACK-SEARCH-{i:03}' for i in range(65)]
    assert len(setup.client.get('/api/packing/items').json())==65
    assert setup.client.get('/api/packing/items',params={'q':'064'}).json()[0]['part_no']=='PACK-SEARCH-064'
    for i in (0,1,2,3,4,5):
        assert setup.client.get(f'/api/packing/item/PACK-SEARCH-{i:03}').status_code==200


def test_non_finished_or_inactive_items_cannot_bypass_search_filter(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        add_item(db,part='SEMI',code='PACK')
        raw=add_item(db,part='RAW',code='PACK');db.get(ItemMasterModel,raw).material_type='RAW'
        inactive=add_finished(db,'INACTIVE');inactive.is_active='N'
        db.commit()
    for part in ('SEMI','RAW','INACTIVE'):
        assert setup.client.get('/api/packing/item/'+part).status_code==404
        assert setup.client.post('/api/packing/scan',json={'part_no':part,'lot_no':'LOT','target_qty':10}).status_code==404
        response=setup.client.post('/api/packing',json={'packing_date':'2026-10-27','part_no':part,
            'box_count':1,'box_qty':10,'allocations':[{'lot_no':'LOT','qty':10}]})
        assert response.status_code==404,response.text
    with setup.sessions() as db:assert db.query(PackingMaster).count()==0


def test_finished_item_without_packing_process_can_scan_and_save(setup):
    from models.production_lot import ProductionLotModel
    setup.app.include_router(router)
    with setup.sessions() as db:
        item=add_finished(db,'VALID-FINISHED');item.production_loc='LT'
        db.flush()
        db.add(ProductionLotModel(lot_no='VALID-SOURCE-LOT',item_id=item.id,part_no=item.part_no,lot_qty=10,status='ACTIVE'))
        db.commit()
    scan=setup.client.post('/api/packing/scan',json={'part_no':'VALID-FINISHED','lot_no':'VALID-SOURCE-LOT','target_qty':10})
    assert scan.status_code==200,scan.text
    assert scan.json()['complete'] and scan.json()['allocated_qty']==10
    saved=setup.client.post('/api/packing',json={'packing_date':'2026-10-27','part_no':'VALID-FINISHED',
        'box_count':1,'box_qty':10,'allocations':[{'lot_no':'VALID-SOURCE-LOT','qty':10}]})
    assert saved.status_code==200,saved.text
    with setup.sessions() as db:
        packing=db.query(PackingMaster).one()
        assert packing.part_no=='VALID-FINISHED' and packing.total_qty==10
