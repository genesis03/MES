import json
from datetime import date,datetime
from types import SimpleNamespace

import pytest

from core.security import get_current_user
from models.equipment import EquipmentMaster
from models.production import ProductionPerformance,ProductionWorkOrder
from models.production_cycle_time import ProductionCycleTime
from models.production_month_plan import ProductionMonthPlanLine
from routers.equipment_oee import router
from services.equipment_oee import calculate
from services.equipment_reports import daily_report,efficiency_report
from test_equipment_oee import plan_ct
from test_production_run_registration import prepare
from test_production_sync import setup,add_item


def test_daily_report_uses_historical_item_cycles_and_only_selected_equipment(setup):
    first,_,_,machine,other_machine=prepare(setup)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,first).item_id
        other_item=add_item(db,part='SECOND-A')
        order=ProductionWorkOrder(work_order_no='OTHER-ITEM',order_date='2026-10-12',item_id=other_item,part_no='SECOND-A',order_qty=1000)
        db.add(order);db.flush()
        plan_ct(db,machine,item,'2026-10-12',30)
        plan_ct(db,machine,other_item,'2026-10-12',60)
        plan_ct(db,other_machine,item,'2026-10-12',30)
        db.add_all([
            ProductionPerformance(work_order_id=first,performance_date='2026-10-12',process_code='LT',shift_type='DAY',equipment_id=machine,good_qty=100,defect_qty=10,setup_qty=2),
            ProductionPerformance(work_order_id=order.id,performance_date='2026-10-12',process_code='LT',shift_type='DAY',equipment_id=machine,good_qty=50,defect_qty=5,setup_qty=3),
            ProductionPerformance(work_order_id=first,performance_date='2026-10-12',process_code='LT',shift_type='DAY',equipment_id=other_machine,good_qty=1000),
        ]);db.commit()
        now=datetime(2026,10,20)
        before=db.query(ProductionPerformance).count()
        report=daily_report(db,'2026-10',machine,now=now)
        day=report['days'][11]
        assert len(report['days'])==31
        assert day['planned_qty']==2000 and day['good_qty']==150 and day['total_qty']==170
        assert day['defect_qty']==15 and day['setup_qty']==5
        assert day['runtime_seconds']==12*3600
        assert day['standard_production_seconds']==6840 and day['standard_good_seconds']==6000 and day['quality_loss_seconds']==840
        assert day['attainment']==pytest.approx(150/2000)
        assert day['defect_rate']==pytest.approx(15/170)
        assert {c['cycle_seconds'] for c in day['cycles']}=={30,60}
        assert not report['days'][10]['has_activity']
        original=next(r for r in calculate(db,date(2026,10,1),date(2026,10,31),now=now)['items'] if r['key']==str(machine))
        assert report['total']['oee']==original['oee']
        assert db.query(ProductionPerformance).count()==before


def test_year_and_process_totals_are_recomputed_and_item_master_ct_does_not_rewrite_history(setup):
    order,_,_,equipment,_=prepare(setup)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        for day,cycle,good,bad,setup_qty in [('2026-01-02',30,100,20,5),('2026-02-02',60,100,0,0),('2026-02-03',60,100,0,0)]:
            plan_ct(db,equipment,item,day,cycle)
            db.add(ProductionPerformance(work_order_id=order,performance_date=day,process_code='LT',shift_type='DAY',equipment_id=equipment,good_qty=good,defect_qty=bad,setup_qty=setup_qty))
        db.add(ProductionCycleTime(item_id=item,seconds=999));db.commit()
        now=datetime(2026,10,20)
        result=efficiency_report(db,2026,1,now=now)
        january,february=[r['summary'] for r in result['months'][:2]]
        # Match the current OEE: combined standard total time / planned time,
        # multiplied by the combined (quantity-based) quality ratio.
        assert result['annual']['oee']==pytest.approx((125*30+200*60)/(3*24*3600)*(300/325))
        assert result['annual']['oee']!=pytest.approx((january['oee']+february['oee'])/2)
        assert result['process_summaries'][0]['oee']==result['summary']['oee']
        assert result['summary']['defect_qty']==20 and result['summary']['setup_qty']==5
        assert result['summary']['attainment']==pytest.approx(.1)
        assert result['months'][10]['summary']['oee'] is None
        db.query(ProductionMonthPlanLine).filter_by(month='2026-01').one().cycle_seconds=999;db.commit()
        again=efficiency_report(db,2026,1,now=now)
        assert again['summary']['oee']==result['summary']['oee']


def test_report_api_validates_managed_equipment_and_leap_month(setup,monkeypatch):
    _,_,_,equipment,_=prepare(setup);setup.app.include_router(router)
    monkeypatch.setattr('services.equipment_reports.report_now',lambda:datetime(2026,10,20))
    with setup.sessions() as db:
        wrong=EquipmentMaster(equipment_code='SER1',equipment_name='세레이션 1호기',process_code='DOT',machine_no='1')
        db.add(wrong);db.commit();wrong_id=wrong.id
    choices=setup.client.get('/api/production/equipment/report-options').json()
    assert {p['process_code'] for p in choices['processes']}=={'LT','TP'}
    assert wrong_id not in {e['id'] for e in choices['equipments']}
    result=setup.client.get('/api/production/equipment/daily',params={'month':'2024-02','equipment_id':equipment})
    assert result.status_code==200 and len(result.json()['days'])==29
    assert setup.client.get('/api/production/equipment/daily',params={'month':'2026-13','equipment_id':equipment}).status_code==422
    assert setup.client.get('/api/production/equipment/daily',params={'month':'2026-10','equipment_id':wrong_id}).status_code==422
    assert setup.client.get('/api/production/equipment/efficiency',params={'year':2026,'month':13}).status_code==422
    assert setup.client.get('/api/production/equipment/efficiency',params={'year':2026,'month':10,'process_code':'DOT'}).status_code==422
    response=setup.client.get('/api/production/equipment/efficiency',params={'year':2026,'month':10})
    assert response.status_code==200 and len(response.json()['months'])==12


def test_reports_respect_separate_menu_permissions(setup):
    _,_,_,equipment,_=prepare(setup);setup.app.include_router(router)
    user=SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{'/production/equipment/daily':'READ'}}))
    setup.app.dependency_overrides[get_current_user]=lambda:user
    assert setup.client.get('/production/equipment/daily').status_code==200
    assert setup.client.get('/api/production/equipment/report-options',params={'view':'daily'}).status_code==200
    assert setup.client.get('/api/production/equipment/daily',params={'month':'2026-10','equipment_id':equipment}).status_code==200
    assert setup.client.get('/production/equipment/efficiency').status_code==403
    assert setup.client.get('/api/production/equipment/report-options',params={'view':'efficiency'}).status_code==403
    assert setup.client.get('/api/production/equipment/efficiency',params={'year':2026,'month':10}).status_code==403


def test_standard_time_is_available_even_when_registered_stops_leave_zero_runtime(setup):
    from models.production_run import ProductionRun,ProductionRunDowntime
    order,_,worker,machine,_=prepare(setup)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        plan_ct(db,machine,item,'2026-10-12',30)
        perf=ProductionPerformance(work_order_id=order,performance_date='2026-10-12',process_code='LT',shift_type='DAY',equipment_id=machine,good_qty=100)
        db.add(perf);db.flush()
        run=ProductionRun(work_order_id=order,performance_id=perf.id,performance_type='MACHINING',performance_date='2026-10-12',process_code='LT',operator_id=worker,operator_name='Worker',equipment_id=machine,equipment_code='LT1',equipment_name='복합선반 1호기',shift_type='DAY',start_time='2026-10-12 08:00',end_time='2026-10-12 20:00',status='COMPLETED')
        db.add(run);db.flush()
        db.add(ProductionRunDowntime(run_id=run.id,type_code='CLEANING',type_name='청소',started_at=run.start_time,ended_at=run.end_time,action=''))
        db.commit()
        day=daily_report(db,'2026-10',machine,now=datetime(2026,10,20))['days'][11]
        assert day['runtime_seconds']==0 and day['performance'] is None
        assert day['standard_production_seconds']==3000 and day['standard_good_seconds']==3000
