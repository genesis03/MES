import pytest
from sqlalchemy import text

from test_production_sync import setup, add_item
from models.models import CommonCodeModel
from models.quality import QualityInboundResult, QualityInboundDefectDetail, QualityInboundLotDefect
from models.subcontract_inbound import SubcontractInboundMaster, SubcontractInboundItem, SubcontractInboundLot
from routers.quality_defect_status import router
from routers.quality_pages import router as pages


def seed(setup, lots=(60, 40), allocated=True):
    setup.app.include_router(router)
    setup.app.include_router(pages)
    with setup.sessions() as db:
        db.execute(text('PRAGMA foreign_keys=OFF'))
        item_id = add_item(db, part='310062-Ag')
        db.add_all([CommonCodeModel(group_code='DEFECT_TYPE', group_name='불량유형', code='D1', code_name='치수', sort_order=1, created_at='2026-09-23'),
                    CommonCodeModel(group_code='DEFECT_TYPE', group_name='불량유형', code='D2', code_name='미도금', sort_order=2, created_at='2026-09-23')])
        for index, status in [(1, 'RECEIVED'), (2, 'CANCELLED')]:
            master = SubcontractInboundMaster(id=index, inbound_no=f'SI-{index}', inbound_date='2026-09-23',
                outbound_id=1, outbound_no='SO-1', order_id=1, order_no='ORDER-1', partner_id=1,
                partner_name='은도금 거래처', processing_type_code='PLATE', processing_type_name='은도금',
                storage_location='TEST', status=status)
            item = SubcontractInboundItem(id=index, inbound_id=index, outbound_item_id=1, order_item_id=1,
                previous_item_id=item_id, item_id=item_id, previous_part_no='310062-B', part_no='310062-Ag',
                part_name='예전 품명', unit='EA', outbound_qty=100, good_qty=100, defect_qty=0)
            db.add(master); db.flush(); db.add(item); db.flush()
            result = QualityInboundResult(id=index, source_type='SUBCONTRACT', inbound_id=index,
                inbound_item_id=index, inspection_status='COMPLETED', defect_qty=5)
            db.add(result); db.flush()
            for code, qty in [('D1', 3), ('D2', 2)]:
                db.add(QualityInboundDefectDetail(result_id=index, source_type='SUBCONTRACT',
                    inbound_item_id=index, defect_type_code=code, defect_qty=qty))
            for position, qty in enumerate(lots):
                lot_no = f'LOT-{index}-{position}'
                db.add(SubcontractInboundLot(inbound_item_id=index, outbound_lot_id=1,
                    source_lot_no='SOURCE', source_qty=qty, good_qty=qty, child_lot_no=lot_no))
                if allocated:
                    db.add(QualityInboundLotDefect(result_id=index, source_type='SUBCONTRACT',
                        inbound_item_id=index, lot_no=lot_no, lot_qty=qty,
                        defect_type_code='D1' if position == 0 else 'D2', defect_qty=3 if position == 0 else 2))
        # A general-purchase result with the same item key must not leak into this report.
        db.add(QualityInboundResult(source_type='GENERAL', inbound_id=1, inbound_item_id=1,
            inspection_status='COMPLETED', defect_qty=99))
        db.commit()


def get(setup, **params):
    response = setup.client.get('/api/quality/defect-status/subcontract', params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_lot_rows_type_columns_totals_filters_and_cancelled_receipts(setup):
    seed(setup)
    data = get(setup)
    assert data['receipt_item_count'] == 1 and data['total'] == 2
    assert [r['lot_no'] for r in data['items']] == ['LOT-1-0', 'LOT-1-1']
    assert [r['inbound_qty'] for r in data['items']] == [60, 40]
    assert [r['good_qty'] for r in data['items']] == [57, 38]
    assert data['totals']['inbound_qty'] == 100 and data['totals']['good_qty'] == 95
    assert data['totals']['defect_qty'] == 5
    assert data['totals']['by_type']['3'] == 3 and data['totals']['by_type']['8'] == 2
    assert len(data['defect_types']) == 13
    assert {c['code'] for c in data['defect_types']} == {str(i) for i in range(1, 14)}
    assert get(setup, part_no='310062', partner_name='은도금', start_date='2026-09-23', end_date='2026-09-23')['total'] == 2
    for params in [{'part_no': 'OTHER'}, {'partner_name': 'OTHER'}, {'end_date': '2026-09-22'}, {'start_date': '2026-09-24'}]:
        assert get(setup, **params)['total'] == 0
    limited = get(setup, limit=1)
    assert limited['truncated'] and limited['total'] == 2 and len(limited['items']) == 1
    assert limited['totals'] == data['totals']
    with setup.sessions() as db:
        db.get(SubcontractInboundMaster, 1).status = 'CANCELLED'; db.commit()
    assert get(setup)['totals']['defect_qty'] == 0


@pytest.mark.parametrize('lots,expected_rows', [((60, 40), 3), ((), 1), ((100,), 1)])
def test_old_item_level_defects_are_not_arbitrarily_split(setup, lots, expected_rows):
    seed(setup, lots=lots, allocated=False)
    data = get(setup)
    assert data['total'] == expected_rows
    assert data['totals']['defect_qty'] == sum(r['defect_qty'] for r in data['items']) == 5
    assert data['totals']['good_qty'] == 95
    if len(lots) == 2:
        assert all(r['good_qty'] is None for r in data['items'])
        assert [r['defect_qty'] for r in data['items']] == [0, 0, 5]
        assert data['items'][-1]['unallocated']
    elif not lots:
        assert data['items'][0]['unallocated'] and data['items'][0]['inbound_qty'] == 100
    else:
        assert data['items'][0]['lot_no'] == 'LOT-1-0'
        assert data['items'][0]['good_qty'] == 95


def test_unclassified_legacy_quantity_keeps_type_totals_balanced(setup):
    seed(setup, allocated=False)
    with setup.sessions() as db:
        db.query(QualityInboundDefectDetail).filter(QualityInboundDefectDetail.defect_type_code == 'D2').delete()
        db.commit()
    data = get(setup)
    assert data['totals']['by_type']['3'] == 3 and data['totals']['by_type']['13'] == 2
    assert sum(data['totals']['by_type'].values()) == 5
    assert {'code': '13', 'name': '기타'} in data['defect_types']


def test_new_page_and_navigation(setup):
    seed(setup)
    response = setup.client.get('/quality/defect-status/subcontract')
    assert response.status_code == 200, response.text
    assert '불량 현황 외주가공' in response.text
    assert 'nav-quality-subcontract-defect-status' in response.text
    assert 'sd-table' in response.text


def test_samples_sum_per_lot_and_unallocated_defects_do_not_show_false_zero_rate(setup):
    seed(setup, allocated=False)
    with setup.sessions() as db:
        lots=db.query(SubcontractInboundLot).filter_by(inbound_item_id=1).order_by(SubcontractInboundLot.id).all()
        lots[0].sample_qty=2;lots[1].sample_qty=1;db.commit()
    data=get(setup)
    assert [r['sample_qty'] for r in data['items']]==[2,1,0]
    assert data['totals']['sample_qty']==3
    assert data['totals']['keep_qty'] is None
    assert all(r['defect_rate'] is None for r in data['items'])
    assert data['totals']['defect_rate']==5


def test_inspected_zero_defect_receipt_increases_denominator_and_cancelled_does_not(setup):
    seed(setup)
    with setup.sessions() as db:
        # Reuse the second receipt, which was initially cancelled in this fixture.
        db.get(SubcontractInboundMaster,2).status='RECEIVED'
        db.get(QualityInboundResult,2).defect_qty=0
        db.query(QualityInboundDefectDetail).filter_by(result_id=2).delete()
        db.query(QualityInboundLotDefect).filter_by(result_id=2).delete()
        db.commit()
    data=get(setup)
    assert data['receipt_item_count']==2 and data['total']==4
    assert data['totals']['inbound_qty']==200 and data['totals']['defect_qty']==5
    assert data['totals']['defect_rate']==2.5
    zero=[r for r in data['items'] if r['inbound_no']=='SI-2']
    assert len(zero)==2 and all(r['defect_rate']==0 for r in zero)
    with setup.sessions() as db:
        db.get(SubcontractInboundMaster,2).status='CANCELLED';db.commit()
    assert get(setup)['totals']['defect_rate']==5
