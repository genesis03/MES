"""Cancelled receipts must not contribute to defect inquiries or totals."""
import pytest
from sqlalchemy import text

from test_production_sync import setup, add_item, DAY, row as source_row
from services import production_sync_service as sync
from models.models import PurchaseInboundItem, PurchaseInboundMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundMaster
from models.quality import QualityInboundDefectDetail, QualityInboundResult
from routers.quality_defect_status import router


@pytest.mark.parametrize('source', ['GENERAL', 'SUBCONTRACT'])
def test_cancelled_receipt_defects_excluded_from_rows_and_totals(setup, source):
    setup.app.include_router(router)
    with setup.sessions() as db:
        # Unrelated order/warehouse references are outside this inquiry test.
        db.execute(text('PRAGMA foreign_keys=OFF'))
        item_id = add_item(db, part='310062-Ag')
        masters = []
        for index, qty in enumerate([2, 4, 11], start=1):
            common = dict(id=index, inbound_no=f'IN-{index}', inbound_date='2026-09-23',
                          partner_id=1, partner_name='검증 거래처')
            if source == 'GENERAL':
                master = PurchaseInboundMaster(**common, status='CANCELLED' if index == 3 else 'CONFIRMED')
                item = PurchaseInboundItem(id=index, inbound_id=index, item_id=item_id,
                    part_no='310062-Ag', unit='EA', inbound_qty=660, supplier_lot_no=f'LOT-{index}',
                    storage_location='TEST', unit_price=0)
            else:
                master = SubcontractInboundMaster(**common, status='CANCELLED' if index == 3 else 'RECEIVED',
                    outbound_id=1, outbound_no='OUT-1', order_id=1, order_no='ORDER-1',
                    processing_type_code='PLATE', processing_type_name='은도금', storage_location='TEST')
                item = SubcontractInboundItem(id=index, inbound_id=index, outbound_item_id=1,
                    order_item_id=1, previous_item_id=item_id, item_id=item_id,
                    previous_part_no='310062-Ag', part_no='310062-Ag', part_name='검증 품목',
                    unit='EA', outbound_qty=660, good_qty=660, defect_qty=0)
            db.add(master); db.flush(); db.add(item); db.flush()
            result = QualityInboundResult(source_type=source, inbound_id=index, inbound_item_id=index,
                inspection_status='COMPLETED', defect_qty=qty)
            db.add(result); db.flush()
            db.add(QualityInboundDefectDetail(result_id=result.id, source_type=source,
                inbound_item_id=index, defect_type_code='TEST', defect_qty=qty))
            masters.append(master)
        db.commit()

        for filter_source in ['ALL', source]:
            response = setup.client.get('/api/quality/defect-status', params={
                'start_date': '2026-09-01', 'end_date': '2026-09-30', 'source': filter_source})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['total'] == 2
            assert data['total_defect_qty'] == 6
            assert sorted(row['defect_qty'] for row in data['items']) == [2, 4]

        for master in masters:
            master.status = 'CANCELLED'
        db.commit()
        data = setup.client.get('/api/quality/defect-status').json()
        assert data['items'] == []
        assert data['total'] == 0
        assert data['total_defect_qty'] == 0
        # Cancellation hides history without deleting quality records.
        assert db.query(QualityInboundResult).count() == 3
        assert db.query(QualityInboundDefectDetail).count() == 3


@pytest.mark.parametrize('inbound_source', ['GENERAL', 'SUBCONTRACT'])
def test_production_scope_excludes_receipt_defects(setup, inbound_source):
    # The legacy ALL API remains available; the production menu sends PRODUCTION.
    test_cancelled_receipt_defects_excluded_from_rows_and_totals(setup, inbound_source)
    with setup.sessions() as db:
        model = PurchaseInboundMaster if inbound_source == 'GENERAL' else SubcontractInboundMaster
        master = db.get(model, 1)
        master.status = 'CONFIRMED' if inbound_source == 'GENERAL' else 'RECEIVED'
        db.commit()
    assert setup.client.get('/api/quality/defect-status').json()['total'] == 1
    data = setup.client.get('/api/quality/defect-status', params={'source': 'PRODUCTION'}).json()
    assert data['total'] == 0 and data['total_defect_qty'] == 0


def test_external_defects_use_type_quantities_exclude_setup_and_support_filters(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        add_item(db, part='310062-A'); db.commit()
    token, _ = sync.claim(DAY, DAY, 'MANUAL')
    rows = [source_row(lot='LX2026032501001', ITEM_NUM='310062-A', PROC_TYPE_NM='복합선반',
        JOB_QTY='100', LOT_QTY='110', FAULT_QTY='5', F10='5',
        DEFECTS=[{'code': 'F4', 'name': '칩 엉김', 'qty': 3}, {'code': 'F2', 'name': '치수', 'qty': 2}],
        SHIFT_NAME='주간', MACHINE_NAME='LT-01', OPERATOR_NAME='작업자'),
        source_row(lot='LX2026032501002', ITEM_NUM='310062-A', PROC_TYPE_NM='복합선반',
            JOB_QTY='100', LOT_QTY='105', FAULT_QTY='0', F10='5', DEFECTS=[])]
    sync.save_day(rows, token)
    url = '/api/quality/defect-status'
    data = setup.client.get(url, params={'start_date': DAY.isoformat(), 'end_date': DAY.isoformat()}).json()
    assert data['total'] == 2 and data['total_defect_qty'] == 5
    record = data['items'][0]
    assert record['source_type'] == 'EXTERNAL_PRODUCTION'
    assert record['equipment_name'] == 'LT-01' and record['shift_name'] == '주간'
    assert record['operator_name'] == '작업자'
    assert record['base_qty'] == 110
    assert record['production_qty'] == 110 and record['good_qty'] == 100 and record['setup_qty'] == 5
    assert len(data['report_defect_types']) == 13
    assert record['report_by_type']['4'] == 3 and record['report_by_type']['2'] == 2
    assert sum(data['report_totals']['by_type'].values()) == 5
    assert record['part_no'] == '310062-A'
    zero = next(r for r in data['items'] if r['defect_qty']==0)
    assert zero['lot_no']=='LX2026032501002' and zero['defect_rate']==0
    assert set(zero['report_by_type'].values())=={0}
    assert data['report_totals']['production_qty']==215
    assert data['report_totals']['defect_rate']==pytest.approx(5/215*100)
    assert {d['code'] for d in record['defect_items']} == {'EXTERNAL:F4', 'EXTERNAL:F2'}
    assert any(t['code'] == 'EXTERNAL:F4' for t in data['defect_types'])
    for filters, count in [({'source': 'EXTERNAL_PRODUCTION', 'defect_type_code': 'EXTERNAL:F4'}, 1),
                           ({'source': 'PRODUCTION_RUN'}, 2), ({'part_no': 'OTHER'}, 0),
                           ({'end_date': '2026-03-24'}, 0)]:
        assert setup.client.get(url, params=filters).json()['total'] == count
    # Updates replace imported records, rather than adding another quality entry.
    rows[0]['DEFECTS'][0]['qty'] = 1
    sync.save_day(rows, token)
    data = setup.client.get(url).json()
    assert data['total'] == 2 and data['total_defect_qty'] == 3


@pytest.mark.parametrize('lot_count', [2, 3])
def test_assembly_group_defects_count_once_without_assigning_them_to_each_lot(setup, lot_count):
    setup.app.include_router(router)
    token, _ = sync.claim(DAY, DAY, 'MANUAL')
    lots = [f'LA2026032500{i}' for i in range(1, lot_count + 1)]
    rows = [source_row(lot=lot, ITEM_NUM='310062-C', PROC_TYPE_NM='조립', JOB_QTY='100',
        LOT_QTY='102', FAULT_QTY='2', F10='0', MACHINE_NAME='AS-01', SHIFT_TYPE='DAY',
        ASSEMBLY_GROUP_LOTS=lots, ASSEMBLY_GROUP_DEFECTS=[{'code': 'F4', 'name': '칩 엉김', 'qty': 2 * lot_count}])
        for lot in lots]
    sync.save_day(rows, token)
    data = setup.client.get('/api/quality/defect-status').json()
    assert data['total'] == lot_count and data['total_defect_qty'] == 2 * lot_count
    assert len(data['items']) == lot_count + 1
    assert [r['lot_no'] for r in data['items'][:-1]] == lots
    for item in data['items'][:-1]:
        assert item['production_qty'] == 102 and item['good_qty'] == 100
        assert item['defect_qty'] == 2 and item['setup_qty'] == 0
        assert set(item['report_by_type'].values()) == {None}
    annotation = data['items'][-1]
    assert annotation['is_unallocated_defect'] and annotation['related_lots'] == lots
    assert annotation['production_qty'] is None and annotation['good_qty'] is None
    assert annotation['defect_qty'] is None and annotation['setup_qty'] is None
    assert annotation['defect_rate'] is None
    assert annotation['report_by_type']['4'] == 2 * lot_count
    totals = data['report_totals']
    assert totals['production_qty'] == 102 * lot_count and totals['good_qty'] == 100 * lot_count
    assert totals['defect_qty'] == totals['by_type']['4'] == 2 * lot_count
    # Type filtering retains the LOT context, without multiplying shared details.
    filtered = setup.client.get('/api/quality/defect-status', params={'defect_type_code': 'EXTERNAL:F4'}).json()
    assert filtered['total'] == lot_count and len(filtered['items']) == lot_count + 1
    assert filtered['report_totals'] == totals
    limited = setup.client.get('/api/quality/defect-status', params={'limit': 1}).json()
    assert limited['truncated'] and limited['report_totals'] == totals


def test_production_filter_combines_internal_and_external_defects(setup):
    from models.production import ProductionWorkOrder
    from models.production_run import ProductionRun, ProductionRunDefect
    setup.app.include_router(router)
    with setup.sessions() as db:
        db.execute(text('PRAGMA foreign_keys=OFF'))
        item_id = add_item(db, part='310062-A')
        order = ProductionWorkOrder(work_order_no='QUALITY-WORK', order_date=DAY.isoformat(),
            item_id=item_id, part_no='310062-A', order_qty=100)
        db.add(order); db.flush()
        for status in ['COMPLETED', 'CANCELLED']:
            run = ProductionRun(work_order_id=order.id, performance_date=DAY.isoformat(),
                process_code='LT', operator_id=1, operator_name='내부 작업자', equipment_id=1,
                equipment_code='LT-01', equipment_name='선반 1호기', shift_type='DAY',
                start_time='08:00', good_qty=98, defect_qty=2, status=status)
            db.add(run); db.flush()
            db.add(ProductionRunDefect(run_id=run.id, defect_type_code='F4',
                defect_type_name='칩 엉김', defect_qty=2))
        db.commit()
    token, _ = sync.claim(DAY, DAY, 'MANUAL')
    sync.save_day([source_row(lot='LX2026032501001', ITEM_NUM='310062-A', PROC_TYPE_NM='복합선반',
        JOB_QTY='97', LOT_QTY='100', FAULT_QTY='3', F10='0',
        DEFECTS=[{'code': 'F4', 'name': '칩 엉김', 'qty': 3}])], token)
    for source in ['ALL', 'PRODUCTION_RUN']:
        data = setup.client.get('/api/quality/defect-status', params={'source': source}).json()
        assert data['total'] == 2 and data['total_defect_qty'] == 5
        assert {r['source_name'] for r in data['items']} == {'생산실적'}
        assert {r['source_type'] for r in data['items']} == {'PRODUCTION_RUN', 'EXTERNAL_PRODUCTION'}
        internal = next(r for r in data['items'] if r['source_type'] == 'PRODUCTION_RUN')
        assert internal['operator_name'] == '내부 작업자' and internal['shift_name'] == '주간'
        assert internal['production_qty'] == 100 and internal['good_qty'] == 98 and internal['setup_qty'] == 0
        assert data['report_totals']['production_qty'] == 200
        assert data['report_totals']['good_qty'] == 195
        assert data['report_totals']['by_type']['4'] == 5


def test_zero_internal_lots_completed_runs_and_post_detection_do_not_duplicate_production(setup):
    from models.production import ProductionPerformance, ProductionWorkOrder
    from models.production_run import ProductionRun
    from models.production_lot import ProductionLotModel
    from models.production_defect import QualityProductionDefect
    setup.app.include_router(router)
    with setup.sessions() as db:
        db.execute(text('PRAGMA foreign_keys=OFF'))
        item_id=add_item(db,part='QUALITY-A')
        order=ProductionWorkOrder(work_order_no='ZERO-QUALITY',order_date=DAY.isoformat(),
            item_id=item_id,part_no='QUALITY-A',order_qty=1000)
        db.add(order);db.flush()
        for index,(good,bad,setup_qty,status) in enumerate([
            (100,0,5,None),(90,10,0,'COMPLETED'),(200,0,0,'CANCELLED'),
            (100,0,0,None),(300,0,0,'IN_PROGRESS')],start=1):
            perf=ProductionPerformance(work_order_id=order.id,performance_date=DAY.isoformat(),
                process_code='LT',good_qty=good,defect_qty=bad,setup_qty=setup_qty)
            db.add(perf);db.flush()
            lot=ProductionLotModel(item_id=item_id,part_no='QUALITY-A',lot_no=f'LOCAL-{index}',
                lot_qty=good,note=f'PERF:{perf.id}|생산실적 자동생성')
            db.add(lot);db.flush()
            if status:
                db.add(ProductionRun(work_order_id=order.id,performance_id=perf.id,
                    performance_date=DAY.isoformat(),process_code='LT',operator_id=1,
                    operator_name='TEST',equipment_id=1,equipment_code='LT-01',equipment_name='선반 1호기',
                    shift_type='DAY',start_time='08:00',good_qty=good,defect_qty=bad,setup_qty=setup_qty,status=status))
            if index==4:
                db.add(QualityProductionDefect(production_lot_id=lot.id,item_id=item_id,lot_no=lot.lot_no,
                    lot_qty=100,defect_date=DAY.isoformat(),defect_qty=2,status='ACTIVE'))
        db.commit()
    params={'source':'PRODUCTION','start_date':DAY.isoformat(),'end_date':DAY.isoformat()}
    data=setup.client.get('/api/quality/defect-status',params=params).json()
    assert data['total']==3
    by_lot={r['lot_no']:r for r in data['items']}
    assert set(by_lot)=={'LOCAL-1','LOCAL-2','LOCAL-4'}
    assert by_lot['LOCAL-1']['defect_qty']==0 and by_lot['LOCAL-1']['defect_rate']==0
    assert by_lot['LOCAL-4']['production_qty']==100 and by_lot['LOCAL-4']['good_qty']==98
    assert by_lot['LOCAL-4']['defect_qty']==2
    totals=data['report_totals']
    assert totals['production_qty']==305 and totals['good_qty']==288 and totals['defect_qty']==12
    assert totals['defect_rate']==pytest.approx(12/305*100)
    limited=setup.client.get('/api/quality/defect-status',params=params|{'limit':1}).json()
    assert limited['report_totals']==totals
