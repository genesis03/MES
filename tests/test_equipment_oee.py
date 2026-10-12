from datetime import date,datetime
import pytest
from test_production_sync import setup, add_item
from test_production_run_registration import prepare
from models.production import ProductionPerformance
from models.production_run import ProductionRun, ProductionRunDowntime
from models.production_cycle_time import ProductionCycleTime
from models.equipment import EquipmentMaster
from models.worker import WorkerMaster
from routers.equipment_oee import router
from services.equipment_oee import calculate
from services.oee_schedule import workday_intervals


def plan_ct(db,equipment,item,day,seconds):
    from models.production_month_plan import ProductionMonthPlanLine,ProductionMonthPlanDay
    line=db.query(ProductionMonthPlanLine).filter_by(month=day[:7],equipment_id=equipment,item_id=item).first()
    if line is None:
        line=ProductionMonthPlanLine(month=day[:7],equipment_id=equipment,item_id=item,cycle_seconds=seconds,expected_oee=.95)
        db.add(line);db.flush()
    db.add(ProductionMonthPlanDay(line_id=line.id,work_date=day,quantity=1000,cycle_seconds=seconds,expected_oee=.95))


def test_week_hours_come_from_entered_detail_days(setup):
    order,_,_,equipment,_=prepare(setup)
    from models.production import ProductionWorkOrder
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        for day in range(12,17):plan_ct(db,equipment,item,f'2026-10-{day:02d}',30)
        db.commit()
        intervals,_=workday_intervals(db,'LT',date(2026,10,12),date(2026,10,18),datetime(2026,10,20),equipment)
        assert sum((b-a).total_seconds() for a,b in intervals)==120*3600
        intervals,_=workday_intervals(db,'LT',date(2026,10,12),date(2026,10,12),datetime(2026,10,12,10),equipment)
        assert sum((b-a).total_seconds() for a,b in intervals)==2*3600


def test_cycle_registration_lookup_validation_and_delete(setup):
    prepare(setup);setup.app.include_router(router)
    with setup.sessions() as db:
        unrelated=add_item(db,part='ASSEMBLY-C',code='ASSY')
    result=setup.client.get('/api/production/cycle-times').json()['items']
    item=result[0]['item_id']
    url='/api/production/cycle-times/'+str(item)
    assert setup.client.put(url,json={'seconds':60}).status_code==200
    assert next(i for i in setup.client.get('/api/production/cycle-times').json()['items'] if i['item_id']==item)['seconds']==60
    assert setup.client.put(url,json={'seconds':0}).status_code==422
    assert setup.client.put(url,json={'seconds':'NaN'}).status_code==422
    assert setup.client.put('/api/production/cycle-times/'+str(unrelated),json={'seconds':60}).status_code==404
    assert setup.client.delete(url).status_code==200
    assert setup.client.get('/api/production/equipment/oee',params={'start_date':'2026-10-13','end_date':'2026-10-12'}).status_code==422


def test_native_oee_missing_cycle_and_overlapping_downtime_union(setup):
    order,_,worker,equipment,_=prepare(setup)
    with setup.sessions() as db:
        from models.production import ProductionWorkOrder
        item=db.get(ProductionWorkOrder,order).item_id
        perf=ProductionPerformance(work_order_id=order,performance_date='2026-10-12',process_code='LT',shift_type='DAY',equipment_id=equipment,equipment_name='복합선반 1호기',good_qty=900,defect_qty=50,setup_qty=50)
        db.add(perf);db.flush()
        e=db.get(EquipmentMaster,equipment);w=db.get(WorkerMaster,worker)
        for number,(start,end) in enumerate([('10:00','10:30'),('10:15','10:45')]):
            run=ProductionRun(work_order_id=order,performance_id=perf.id if number==0 else None,performance_type='MACHINING',performance_date='2026-10-12',process_code='LT',shift_type='DAY',operator_id=worker,operator_name=w.worker_name,equipment_id=equipment,equipment_code=e.equipment_code,equipment_name=e.equipment_name,start_time='2026-10-12 09:00',end_time='2026-10-12 18:00',status='COMPLETED')
            db.add(run);db.flush()
            db.add(ProductionRunDowntime(run_id=run.id,type_code='2',type_name='툴교환',started_at='2026-10-12 '+start,ended_at='2026-10-12 '+end,action=''))
        db.commit()
        result=calculate(db,date(2026,10,12),date(2026,10,17),now=datetime(2026,10,19))
        row=next(r for r in result['items'] if r['key']==str(equipment))
        assert row['planned_seconds']==0
        assert row['downtime_seconds']==0
        assert row['oee'] is None and '생산계획 C/T 미등록' in row['issues'][0]
        db.add(ProductionCycleTime(item_id=item,seconds=60));plan_ct(db,equipment,item,'2026-10-12',60);db.commit()
        result=calculate(db,date(2026,10,12),date(2026,10,17),now=datetime(2026,10,19))
        row=next(r for r in result['items'] if r['key']==str(equipment))
        assert row['total_qty']==1000 and row['good_qty']==900
        assert row['quality']==.9
        assert row['oee']==pytest.approx(900*60/(24*3600))
        assert row['planned_seconds']==24*3600
        assert row['downtime_seconds']==45*60
        assert row['runtime_seconds']==24*3600-45*60


def test_external_performance_uses_registered_machine_and_item(setup):
    from test_production_sync import row, DAY
    from services import production_sync_service as sync
    from models.production_sync import ProductionSyncItemMap, ProductionSyncProcessMap
    from models.production import ProductionWorkOrder
    order,_,_,equipment,_=prepare(setup)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        db.add(ProductionCycleTime(item_id=item,seconds=30))
        plan_ct(db,equipment,item,DAY.isoformat(),30)
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A',source_process_name='복합선반',item_id=item))
        db.add(ProductionSyncProcessMap(source_name='복합선반',process_code='LT',performance_type='MACHINING'))
        db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL')
    sync.save_day([row(lot='LX20260325011',PROC_TYPE_NM='복합선반')],token)
    with setup.sessions() as db:
        result=calculate(db,DAY,DAY,now=datetime(2026,3,26,12))
        machine=next(r for r in result['items'] if r['key']==str(equipment))
        assert machine['total_qty']==331 and machine['good_qty']==216
        assert machine['parts'][0]['sources']==['외부']
        assert machine['oee']==pytest.approx(216*30/(24*3600))
        partial=calculate(db,DAY,DAY,now=datetime(2026,3,26))
        assert partial['planned_seconds']==16*3600


def test_cycle_api_enforces_readonly_menu_permission(setup):
    import json
    from types import SimpleNamespace
    from core.security import get_current_user
    prepare(setup);setup.app.include_router(router)
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='readonly',role='USER',permissions=json.dumps({'menu_access':{'/basic-info/items':'READ'}}))
    assert setup.client.get('/api/production/cycle-times').status_code==200
    assert setup.client.put('/api/production/cycle-times/1',json={'seconds':60}).status_code==403
    assert setup.client.get('/api/production/equipment/oee',params={'start_date':'2026-10-12','end_date':'2026-10-12'}).status_code==403
