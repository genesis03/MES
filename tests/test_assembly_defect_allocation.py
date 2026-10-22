import io
import json
import pytest
from openpyxl import load_workbook
from test_production_sync import setup, DAY, row as source_row, add_item
from test_daily_job_report import cell
from models.models import ProcessModel
from models.production_sync import ExternalProductionRecord
from routers.quality_defect_status import router as quality_router
from routers.daily_job_report import router as daily_router
from services import production_sync_service as sync
from services.daily_job_report import build_workbook


def seed(setup, change=None):
    setup.app.include_router(quality_router); setup.app.include_router(daily_router)
    with setup.sessions() as db:
        add_item(db, part='310196-C')
        db.add(ProcessModel(process_code='ASSY', process_name='조립', is_active='Y', created_at=DAY.isoformat())); db.commit()
    lots = ['LA20260921011', 'LA20260921012']
    details = [{'code': 'F1', 'name': '찍힘', 'qty': 2}, {'code': 'F3', 'name': '치수', 'qty': 3}]
    rows = [source_row(lot=lot, ITEM_NUM='310196-C', PROC_TYPE_NM='캡조립',
        JOB_QTY=str(good), FAULT_QTY=str(bad), LOT_QTY=str(good+bad), F10='0',
        MACHINE_NAME='AS-01', SHIFT_TYPE='DAY', SHIFT_NAME='주간',
        ASSEMBLY_GROUP_LOTS=lots, ASSEMBLY_GROUP_DEFECTS=details)
        for lot, good, bad in zip(lots, [1080, 5], [0, 5])]
    if change:
        change(rows)
    token, _ = sync.claim(DAY, DAY, 'MANUAL'); sync.save_day(rows, token)
    return rows


def test_existing_import_resolves_one_defective_lot_consistently_in_all_reports(setup):
    seed(setup)
    params = {'start_date': DAY.isoformat(), 'end_date': DAY.isoformat()}
    performance = setup.client.get('/api/production/performance-status', params=params).json()
    assert len(performance) == 2
    target = next(row for row in performance if row['output_lot_no'] == 'LA20260921012')
    other = next(row for row in performance if row['output_lot_no'] == 'LA20260921011')
    assert target['defects'] == [{'code': 'F1', 'name': '찍힘', 'qty': 2}, {'code': 'F3', 'name': '치수', 'qty': 3}]
    assert other['defects'] == []
    assert not target['assembly_group_defects'] and not other['assembly_group_defects']
    quality = setup.client.get('/api/quality/defect-status', params={'source': 'PRODUCTION', 'process_code': 'ASSY'}).json()
    assert quality['total'] == 2 and quality['total_defect_qty'] == 5
    defective = next(r for r in quality['items'] if r['lot_no']=='LA20260921012')
    assert defective['report_by_type']['1'] == 2
    assert defective['report_by_type']['3'] == 3
    assert next(r for r in quality['items'] if r['lot_no']=='LA20260921011')['defect_rate']==0
    assert quality['report_totals']['defect_rate']==pytest.approx(5/1090*100)
    assert not any(row.get('is_unallocated_defect') for row in quality['items'])
    assert setup.client.get('/api/quality/defect-status', params={'source': 'PRODUCTION', 'process_code': 'OTHER'}).json()['total'] == 0
    daily = setup.client.get('/api/production/daily-job-report', params={'day': DAY.isoformat()}).json()['items']
    row = next(r for r in daily if r['output_lot_no'] == 'LA20260921012')
    assert row['defects'] == target['defects'] and not row['assembly_group_defects']
    response = setup.client.post('/api/production/daily-job-report/excel', json={'day': DAY.isoformat(), 'keys': [row['key']]})
    assert response.status_code == 200, response.text
    sheet = load_workbook(io.BytesIO(response.content)).active
    sheet._report_cell_map = build_workbook([row]).active._report_cell_map
    assert cell(sheet, 'AA15').value == 2 and cell(sheet, 'AD15').value == '찍힘'
    assert cell(sheet, 'AA16').value == 3 and cell(sheet, 'AD16').value == '치수'
    with setup.sessions() as db:
        # Resolve at read time: keep the imported raw evidence unchanged.
        assert all(json.loads(r.raw_json)['ASSEMBLY_GROUP_DEFECTS'] for r in db.query(ExternalProductionRecord))


@pytest.mark.parametrize('case', ['multiple_bad_lots', 'wrong_total', 'missing_lot', 'different_machine', 'different_details'])
def test_ambiguous_or_incomplete_group_stays_unallocated(setup, case):
    def change(rows):
        if case == 'multiple_bad_lots':
            rows[0].update(FAULT_QTY='2', LOT_QTY='1082')
            rows[1].update(FAULT_QTY='3', LOT_QTY='8')
        elif case == 'wrong_total':
            rows[1].update(FAULT_QTY='6', LOT_QTY='11')
        elif case == 'missing_lot':
            rows.pop(0)
        elif case == 'different_machine':
            rows[0]['MACHINE_NAME'] = 'AS-02'
        elif case == 'different_details':
            rows[0]['ASSEMBLY_GROUP_DEFECTS'] = [{'code': 'F3', 'name': '치수', 'qty': 5}]
    seed(setup, change)
    data = setup.client.get('/api/production/performance-status').json()
    assert data and all(row['assembly_group_defects'] for row in data)
    assert all(not row['defects'] for row in data)
