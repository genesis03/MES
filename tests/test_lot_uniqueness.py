import hashlib
import json

import pytest

from test_production_sync import setup, row, DAY
from test_packing_sync import row as packing_row, add_finished, enable_routes
from models.production_lot import ProductionLotModel
from models.production_sync import ExternalProductionRecord, ProductionSyncItemMap
from models.packing_sync import ExternalPackingRecord
from services import production_sync_service as sync
from services.production_sync_client import SyncError


def test_production_batch_duplicates_ignore_item_job_and_process(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL')
    with pytest.raises(SyncError,match='생산 LOT'):
        sync.save_day([row(),row(ITEM_NUM='OTHER',JOB_NUM='OTHER-JOB',PROC_TYPE_NM='OTHER-PROCESS')],token)
    with setup.sessions() as db:assert db.query(ExternalProductionRecord).count()==0


def test_production_metadata_change_updates_same_lot(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    with setup.sessions() as db:record_id=db.query(ExternalProductionRecord).one().id
    assert sync.save_day([row(JOB_NUM='CORRECTED',PROC_TYPE_NM='Corrected process',JOB_QTY='217')],token)['updated']==1
    with setup.sessions() as db:
        record=db.query(ExternalProductionRecord).one()
        assert record.id==record_id and record.job_no=='CORRECTED' and record.job_qty=='217'


def test_same_production_lot_different_source_item_cannot_overwrite(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    with pytest.raises(SyncError,match='다른 외부 품번'):
        sync.save_day([row(ITEM_NUM='OTHER',JOB_NUM='OTHER',JOB_QTY='999')],token)
    with setup.sessions() as db:
        record=db.query(ExternalProductionRecord).one()
        assert record.part_no=='EXTERNAL-A' and record.job_qty=='216'


def test_native_production_collision_rejected_even_when_part_is_different(setup):
    with setup.sessions() as db:
        item=add_finished(db,'OTHER-A')
        db.add(ProductionLotModel(item_id=item.id,part_no=item.part_no,lot_no='TEST-LOT-001',lot_qty=12,status='ACTIVE'));db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL')
    with pytest.raises(SyncError,match='이미 사용'):
        sync.save_day([row()],token)
    with setup.sessions() as db:assert db.query(ExternalProductionRecord).count()==0


def test_legacy_source_key_upgrades_without_creating_another_record(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    legacy=hashlib.sha256(json.dumps([row()['LOT_NUM'],row()['JOB_NUM'],row()['PROC_TYPE_NM']],ensure_ascii=False).encode()).hexdigest()
    with setup.sessions() as db:
        record=db.query(ExternalProductionRecord).one();record_id=record.id;record.source_key=legacy;db.commit()
    assert sync.save_day([row()],token)['unchanged']==1
    with setup.sessions() as db:
        record=db.query(ExternalProductionRecord).one()
        assert record.id==record_id and record.source_key==sync.key_for(row())


def test_existing_cross_part_production_collision_is_flagged_and_not_double_counted(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    with setup.sessions() as db:
        item=add_finished(db,'OTHER-A')
        db.add(ProductionLotModel(item_id=item.id,part_no=item.part_no,lot_no=row()['LOT_NUM'],lot_qty=12,status='ACTIVE'));db.commit()
    result=setup.client.get('/api/production/external-sync/records').json()['rows'][0]
    assert result['lot_conflict'] and any('내부 LOT' in note for note in result['notes'])
    assert not any(r['record_source']=='EXTERNAL' for r in setup.client.get('/api/production/performance-status').json())


def test_packing_same_lot_different_items_allowed(setup):
    with setup.sessions() as db:
        add_finished(db,'FIRST');add_finished(db,'SECOND');db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    assert sync.save_day([packing_row(part='FIRST'),packing_row(part='SECOND')],token,'packing')['inserted']==2
    assert sync.save_day([packing_row(part='FIRST'),packing_row(part='SECOND')],token,'packing')['unchanged']==2


def test_packing_manual_mapping_cannot_create_duplicate_item_lot(setup):
    enable_routes(setup)
    with setup.sessions() as db:
        first=add_finished(db,'FIRST');second=add_finished(db,'SECOND');db.commit();first_id=first.id;second_id=second.id
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    sync.save_day([packing_row(part='SOURCE1'),packing_row(part='SOURCE2')],token,'packing')
    base='/api/production/external-sync/item-maps'
    assert setup.client.put(base,json={'source_part_no':'SOURCE1','source_process':'포장','item_id':first_id}).status_code==200
    assert setup.client.put(base,json={'source_part_no':'SOURCE2','source_process':'포장','item_id':first_id}).status_code==409
    assert setup.client.put(base,json={'source_part_no':'SOURCE2','source_process':'포장','item_id':second_id}).status_code==200


def test_packing_different_source_parts_same_mes_identity_rejected(setup):
    with setup.sessions() as db:
        item=add_finished(db,'MES')
        db.add_all([ProductionSyncItemMap(source_part_no=p,source_process_name='포장',item_id=item.id) for p in ('SOURCE1','SOURCE2')]);db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    with pytest.raises(SyncError,match='중복'):
        sync.save_day([packing_row(part='SOURCE1'),packing_row(part='SOURCE2')],token,'packing')
    with setup.sessions() as db:assert db.query(ExternalPackingRecord).count()==0


def test_case_and_whitespace_do_not_create_new_production_identity(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    assert sync.save_day([row(LOT_NUM=' '+row()['LOT_NUM'].lower()+' ')],token)['updated']==1
    with setup.sessions() as db:assert db.query(ExternalProductionRecord).count()==1


def test_legacy_duplicate_production_lots_are_flagged_and_not_merged(setup):
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    with setup.sessions() as db:
        original=db.query(ExternalProductionRecord).one()
        fields={column.name:getattr(original,column.name) for column in ExternalProductionRecord.__table__.columns if column.name!='id'}
        fields.update(source_key='legacy-duplicate',job_no='OTHER-JOB',part_no='OTHER-A')
        db.add(ExternalProductionRecord(**fields));db.commit()
    results=setup.client.get('/api/production/external-sync/records').json()['rows']
    assert len(results)==2 and all(r['lot_conflict'] for r in results)
    assert not any(r['record_source']=='EXTERNAL' for r in setup.client.get('/api/production/performance-status').json())
    with pytest.raises(SyncError,match='기존 외부 생산 LOT'):
        sync.save_day([row()],token)
    with setup.sessions() as db:assert db.query(ExternalProductionRecord).count()==2


def test_production_global_identity_also_blocks_packing_on_another_item(setup):
    with setup.sessions() as db:
        item=add_finished(db,'OTHER');add_finished(db,'SOURCE')
        db.add(ProductionLotModel(item_id=item.id,part_no=item.part_no,lot_no=packing_row()['LOT_NUM'],lot_qty=12,status='ACTIVE'));db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    with pytest.raises(SyncError,match='생산 LOT'):
        sync.save_day([packing_row()],token,'packing')
    with setup.sessions() as db:assert db.query(ExternalPackingRecord).count()==0
