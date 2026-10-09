"""External printing revalidates source data and never creates internal stock."""
import json
import re
import pytest
from test_production_sync import setup, DAY, row, add_item
from test_packing_sync import row as packing_row, add_finished
from services import production_sync_service as sync
from routers.internal_labels import router
from models.production_sync import ExternalProductionRecord, ProductionSyncItemMap
from models.packing_sync import ExternalPackingRecord
from models.production_lot import ProductionLotModel
from models.packing import PackingMaster, PackingBox


def labels(response):
    assert response.status_code == 200, response.text
    return json.loads(re.search(r'const INTERNAL_LABELS\s*=\s*(.*);', response.text)[1])


def test_production_label_mapping_reprint_and_fresh_validation(setup):
    setup.app.include_router(router)
    token, _ = sync.claim(DAY, DAY, 'MANUAL')
    sync.save_day([row(lot='LX20260325016')], token)
    with setup.sessions() as db:
        item_id = add_item(db)
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A', source_process_name='Test machining', item_id=item_id))
        db.commit()
        rid = db.query(ExternalProductionRecord).one().id
    url = f'/internal-labels/external-production/{rid}?auto=0'
    first = labels(setup.client.get(url))[0]
    assert first['part_no'] == 'LOCAL-B' and first['lot_no'] == 'LX20260325016'
    assert first['qty'] == 216 and first['equipment'] == '1호기'
    assert [float(x['value']) for x in first['extras']] == [216, 0, 115, 331]
    assert labels(setup.client.get(url))[0] == first
    with setup.sessions() as db:
        assert db.query(ProductionLotModel).count() == 0
        db.query(ProductionSyncItemMap).delete(); db.commit()
    assert setup.client.get(url).status_code == 409
    assert setup.client.get('/internal-labels/external-production/99999').status_code == 404


@pytest.mark.parametrize('shipped,allowed', [('80', True), ('240', False)])
def test_packing_label_original_quantity_and_no_internal_creation(setup, shipped, allowed):
    setup.app.include_router(router)
    with setup.sessions() as db:
        add_finished(db); db.commit()
    token, _ = sync.claim(DAY, DAY, 'MANUAL', 'packing')
    sync.save_day([packing_row(JOB_QTY=shipped, CONFIRM_DATE='20260325')], token, 'packing')
    with setup.sessions() as db:
        rid = db.query(ExternalPackingRecord).one().id
    url = f'/internal-labels/external-packing/{rid}?auto=0'
    response = setup.client.get(url)
    if allowed:
        first = labels(response)[0]
        assert first['qty'] == 240 and first['lot_no'] == '26100501001'
        assert first['part_no'] == 'SOURCE'
        assert labels(setup.client.get(url))[0] == first
        with setup.sessions() as db:
            db.query(ExternalPackingRecord).update({'shipment_qty': '240'}); db.commit()
        assert setup.client.get(url).status_code == 409
    else:
        assert response.status_code == 409
    with setup.sessions() as db:
        assert db.query(PackingMaster).count() == db.query(PackingBox).count() == 0


def test_duplicate_production_lot_blocks_label(setup):
    setup.app.include_router(router)
    token, _ = sync.claim(DAY, DAY, 'MANUAL')
    sync.save_day([row()], token)
    with setup.sessions() as db:
        item_id = add_item(db)
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A', source_process_name='Test machining', item_id=item_id))
        source = db.query(ExternalProductionRecord).one()
        rid = source.id
        db.add(ExternalProductionRecord(**{column.name: getattr(source, column.name) for column in source.__table__.columns if column.name not in ('id', 'source_key')}, source_key='legacy-duplicate'))
        db.commit()
    assert setup.client.get(f'/internal-labels/external-production/{rid}').status_code == 409
