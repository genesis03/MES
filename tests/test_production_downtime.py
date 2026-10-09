from test_production_sync import setup, DAY, ADMIN
from test_production_run_registration import prepare
from models.production_run import ProductionRun
from models.production import ProductionPerformance
from models.production_change_type_migration import ensure_production_change_type_codes
from routers.daily_job_report import router as report_router
from services.daily_job_report import build_workbook, workbook_html
from test_daily_job_report import cell, report_row


def start(setup):
    order,_,worker,equipment,_=prepare(setup)
    ensure_production_change_type_codes(setup.engine)
    response=setup.client.post('/api/production-run/start',json=dict(work_order_id=order,performance_date=DAY.isoformat(),process_code='LT',operator_id=worker,equipment_id=equipment,shift_type='DAY'))
    assert response.status_code==200,response.text
    id=response.json()['id']
    with setup.sessions() as db:
        run=db.get(ProductionRun,id);run.start_time='2026-03-25 09:00';run.end_time='2026-03-25 18:00';db.commit()
    return id


def data(**changes):
    return dict(type_code='2',started_at='2026-03-25T10:00',ended_at='2026-03-25T10:30',action='툴 교체 후 확인',quality_confirmed=True)|changes


def test_downtime_crud_bounds_overlap_and_assembly_rejection(setup):
    run_id=start(setup);url=f'/api/production-run/{run_id}/downtimes'
    assert len(setup.client.get('/api/production-run/downtime-types').json())==10
    response=setup.client.post(url,json=data());assert response.status_code==200,response.text
    saved=response.json();assert saved['minutes']==30 and saved['type_name']=='툴교환'
    assert setup.client.post(url,json=data()).status_code==409
    for changes in [dict(started_at='2026-03-25T08:59'),dict(ended_at='2026-03-25T18:01'),dict(ended_at='2026-03-25T09:59'),dict(started_at='invalid'),dict(type_code='UNKNOWN')]:
        assert setup.client.post(url,json=data(**changes)).status_code==422
    # Adjacent intervals are allowed; editing the original must not overlap the neighbour.
    nextrow=setup.client.post(url,json=data(started_at='2026-03-25T10:30',ended_at='2026-03-25T11:00'))
    assert nextrow.status_code==200,nextrow.text
    detail=url+'/'+str(saved['id'])
    assert setup.client.put(detail,json=data(ended_at='2026-03-25T10:31')).status_code==409
    assert setup.client.put(detail,json=data(ended_at='2026-03-25T10:20',quality_confirmed=False)).json()['minutes']==20
    # Editing the parent times cannot exclude already saved downtime.
    update=setup.client.put(f'/api/production-run/{run_id}/details',json={'start_time':'2026-03-25 11:00','good_qty':100})
    assert update.status_code==422,update.text
    assert setup.client.delete(detail).status_code==200
    assert len(setup.client.get(f'/api/production-run/{run_id}').json()['downtimes'])==1
    with setup.sessions() as db:
        run=db.get(ProductionRun,run_id);assert run.good_qty==0
        run.performance_type='ASSEMBLY';db.commit()
    assert setup.client.post(url,json=data()).status_code==409
    with setup.sessions() as db:
        run=db.get(ProductionRun,run_id);run.performance_type='MACHINING';run.status='COMPLETED';db.commit()
    assert setup.client.post(url,json=data()).status_code==409
    assert setup.client.delete(url+'/'+str(nextrow.json()['id'])).status_code==409


def test_completed_native_downtime_connects_to_daily_report_and_deletes_with_run(setup):
    run_id=start(setup);setup.app.include_router(report_router)
    response=setup.client.post(f'/api/production-run/{run_id}/downtimes',json=data());assert response.status_code==200
    with setup.sessions() as db:
        run=db.get(ProductionRun,run_id)
        performance=ProductionPerformance(work_order_id=run.work_order_id,performance_date=DAY.isoformat(),process_code='LT',shift_type='DAY',good_qty=100,defect_qty=0,setup_qty=0)
        db.add(performance);db.flush();run.performance_id=performance.id;run.status='COMPLETED';db.commit()
    response=setup.client.get('/api/production/daily-job-report',params={'day':DAY.isoformat()});assert response.status_code==200,response.text
    row=response.json()['items'][0];assert len(row['downtimes'])==1
    sheet=build_workbook([row]).active
    assert cell(sheet,'C27').value=='2'
    assert cell(sheet,'E27').value=='10:00~10:30 (30분)'
    assert cell(sheet,'J27').value=='툴 교체 후 확인'
    assert cell(sheet,'P27').value=='☑'
    assert row['total_qty']==100
    from models.production_run import ProductionRunDowntime
    with setup.sessions() as db:
        db.delete(db.get(ProductionRun,run_id));db.commit()
        assert db.query(ProductionRunDowntime).count()==0


def test_more_than_five_downtimes_have_continuation_page():
    row=report_row('LOT')
    row['downtimes']=[dict(type_code='2',started_at=f'2026-10-09 {9+i:02d}:00',ended_at=f'2026-10-09 {9+i:02d}:10',minutes=10,action=f'조치 {i}',quality_confirmed=False) for i in range(6)]
    workbook=build_workbook([row]);assert len(workbook.worksheets)==2
    assert cell(workbook.worksheets[1],'J27').value=='조치 5'
    assert '비가동 계속' in workbook_html(workbook)
