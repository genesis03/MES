"""The packing work screen includes validated external waiting balances."""
from datetime import datetime
from test_production_sync import setup, DAY
from test_packing_sync import row, add_finished
from models.packing import PackingMaster, PackingBox
from models.packing_sync import ExternalPackingRecord, PackingSourcePresence
from routers.packing import router
from services import production_sync_service as sync


def import_rows(rows):
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    sync.save_day(rows,token,'packing')
    return token


def native_box(db,item,lot='26091501006'):
    master=PackingMaster(packing_no='PK1',packing_date='2026-03-25',item_id=item.id,part_no=item.part_no,box_count=1,box_qty=120,total_qty=120)
    db.add(master);db.flush();db.add(PackingBox(packing_id=master.id,box_no=1,package_lot_no=lot,box_qty=120))


def test_finished_part_waiting_includes_11_external_boxes_and_native(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        item=add_finished(db,'310186-1');native_box(db,item);db.commit()
    import_rows([row(part='310186-1',lot=f'26100801{i:03}',LOT_QTY='120') for i in range(1,12)])
    records=setup.client.get('/api/packing/records?part_no=310186-1').json()
    assert len(records)==12 and sum(x['total_qty'] for x in records)==1440
    external=[x for x in records if x['record_source']=='EXTERNAL']
    assert len(external)==11 and sum(x['total_qty'] for x in external)==1320
    assert all(not box['can_cancel'] and box['id'] is None for x in external for box in x['waiting_boxes'])
    with setup.sessions() as db:assert db.query(PackingBox).count()==1


def test_remaining_shipped_missing_unlinked_and_other_part_filter(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        add_finished(db);add_finished(db,'OTHER');db.commit()
    token=import_rows([row(lot='PARTIAL',JOB_QTY='80'),row(lot='FULL',JOB_QTY='240'),
        row(lot='MISSING'),row(part='UNKNOWN',lot='UNLINKED'),row(part='OTHER',lot='OTHERLOT')])
    with setup.sessions() as db:
        rid=db.query(ExternalPackingRecord).filter_by(lot_no='MISSING').one().id
        db.add(PackingSourcePresence(record_id=rid,present=False,checked_at=datetime.now()));db.commit()
    records=setup.client.get('/api/packing/records?part_no=SOURCE').json()
    assert len(records)==1 and records[0]['waiting_lots']==['PARTIAL']
    assert records[0]['total_qty']==160
    sync.save_day([row(lot='PARTIAL',JOB_QTY='100')],token,'packing')
    assert setup.client.get('/api/packing/records?part_no=SOURCE').json()[0]['total_qty']==140


def test_legacy_native_external_conflict_is_not_double_counted(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:add_finished(db);db.commit()
    import_rows([row(lot='SAME',LOT_QTY='120')])
    with setup.sessions() as db:
        from models.models import ItemMasterModel
        native_box(db,db.query(ItemMasterModel).filter_by(part_no='SOURCE').one(),lot='SAME');db.commit()
    records=setup.client.get('/api/packing/records?part_no=SOURCE').json()
    assert len(records)==1 and records[0]['total_qty']==120 and records[0]['record_source']=='MES'
