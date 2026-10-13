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
        assert row['oee'] is None and any('생산계획 C/T 미등록' in issue for issue in row['issues'])
        db.add(ProductionCycleTime(item_id=item,seconds=60));plan_ct(db,equipment,item,'2026-10-12',60);db.commit()
        result=calculate(db,date(2026,10,12),date(2026,10,17),now=datetime(2026,10,19))
        row=next(r for r in result['items'] if r['key']==str(equipment))
        assert row['total_qty']==1000 and row['good_qty']==900
        assert row['quality']==.9
        assert row['oee']==pytest.approx(900*60/(24*3600))
        assert row['planned_seconds']==24*3600
        assert row['registered_downtime_seconds']==45*60
        assert row['downtime_seconds']==12*3600
        assert row['runtime_seconds']==12*3600


@pytest.mark.parametrize('lot,machine_no',[('LX20260325011','1'),('LX202603250612','6'),('LX2026032506123','6')])
def test_external_performance_uses_registered_machine_and_item(setup,lot,machine_no):
    from test_production_sync import row, DAY
    from services import production_sync_service as sync
    from models.production_sync import ProductionSyncItemMap, ProductionSyncProcessMap
    from models.production import ProductionWorkOrder
    order,_,_,equipment,_=prepare(setup)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        db.get(EquipmentMaster,equipment).machine_no=machine_no
        db.add(ProductionCycleTime(item_id=item,seconds=30))
        plan_ct(db,equipment,item,DAY.isoformat(),30)
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A',source_process_name='복합선반',item_id=item))
        db.add(ProductionSyncProcessMap(source_name='복합선반',process_code='LT',performance_type='MACHINING'))
        db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL')
    sync.save_day([row(lot=lot,PROC_TYPE_NM='복합선반')],token)
    with setup.sessions() as db:
        result=calculate(db,DAY,DAY,now=datetime(2026,3,26,12))
        machine=next(r for r in result['items'] if r['key']==str(equipment))
        assert machine['total_qty']==331 and machine['good_qty']==216
        assert machine['parts'][0]['sources']==['외부']
        assert machine['oee']==pytest.approx(216*30/(24*3600))
        partial=calculate(db,DAY,DAY,now=datetime(2026,3,26))
        assert partial['planned_seconds']==16*3600
        from models.production_month_plan import ProductionMonthPlanDay
        db.query(ProductionMonthPlanDay).filter_by(work_date=DAY.isoformat()).delete();db.commit()
        filtered=calculate(db,DAY,DAY,now=datetime(2026,3,26,12),exclude_unplanned=True)
        assert filtered['summary']['excluded_records']==1
        assert filtered['summary']['excluded_total_qty']==331
        assert filtered['summary']['total_qty']==0 and filtered['summary']['runtime_seconds']==0
        assert calculate(db,DAY,DAY,now=datetime(2026,3,26,12))['summary']['total_qty']==331


def test_cycle_api_enforces_readonly_menu_permission(setup):
    import json
    from types import SimpleNamespace
    from core.security import get_current_user
    prepare(setup);setup.app.include_router(router)
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='readonly',role='USER',permissions=json.dumps({'menu_access':{'/basic-info/items':'READ'}}))
    assert setup.client.get('/api/production/cycle-times').status_code==200
    assert setup.client.put('/api/production/cycle-times/1',json={'seconds':60}).status_code==403
    assert setup.client.get('/api/production/equipment/oee',params={'start_date':'2026-10-12','end_date':'2026-10-12'}).status_code==403


@pytest.mark.parametrize('process,shifts,minutes',[
    ('LT',['DAY'],720),('LT',['DAY','NIGHT'],1440),
    ('LT',['DAY','DAY','NIGHT'],1440),('LT',[None,None,None],1440),
    ('TP',['DAY'],620),('TP',['NIGHT'],610),('TP',['DAY','NIGHT'],1230),
    ('TP',[None],620),('TP',[None,None,None],1230),('TP',[],0),
])
def test_equipment_shift_runtime_and_remaining_downtime(setup,process,shifts,minutes):
    from models.production import ProductionWorkOrder
    order,_,_,equipment,_=prepare(setup)
    with setup.sessions() as db:
        db.get(EquipmentMaster,equipment).process_code=process
        item=db.get(ProductionWorkOrder,order).item_id
        plan_ct(db,equipment,item,'2026-10-12',30)
        for shift in shifts:
            db.add(ProductionPerformance(work_order_id=order,performance_date='2026-10-12',process_code=process,shift_type=shift,equipment_id=equipment,good_qty=100))
        db.commit()
        row=next(r for r in calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))['items'] if r['key']==str(equipment))
        base=24*3600-(210*60 if process=='TP' else 0)
        assert row['runtime_seconds']==minutes*60
        assert row['planned_seconds']==pytest.approx(base)
        assert row['planned_stop_seconds']==pytest.approx(210*60 if process=='TP' else 0)
        assert row['downtime_seconds']==pytest.approx(base-minutes*60)


def test_all_only_managed_processes_and_same_machine_number_separated(setup):
    from models.models import ProcessModel
    from models.production import ProductionWorkOrder
    from test_production_sync import row,DAY
    from services import production_sync_service as sync
    from models.production_sync import ProductionSyncItemMap,ProductionSyncProcessMap
    order,_,_,equipment,_=prepare(setup)
    setup.app.include_router(router)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        tap=EquipmentMaster(equipment_code='TP1',equipment_name='탭핑 1호기',process_code='TP',machine_no='1')
        db.add(ProcessModel(process_code='SER',process_name='세레이션',is_active='Y',created_at='2026-01-01'))
        db.add(tap);db.flush()
        db.add(EquipmentMaster(equipment_code='SER1',equipment_name='세레이션 1호기',process_code='SER',machine_no='1'))
        for e in [equipment,tap.id]:plan_ct(db,e,item,DAY.isoformat(),30)
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A',source_process_name='복합선반',item_id=item))
        db.add(ProductionSyncItemMap(source_part_no='EXTERNAL-A',source_process_name='탭핑',item_id=item))
        db.add(ProductionSyncProcessMap(source_name='복합선반',process_code='LT'))
        db.add(ProductionSyncProcessMap(source_name='탭핑',process_code='TP'))
        db.commit();tap_id=tap.id
    token,_=sync.claim(DAY,DAY,'MANUAL')
    sync.save_day([row(lot='LX20260325011',PROC_TYPE_NM='복합선반'),row(lot='LB20260325011',PROC_TYPE_NM='탭핑')],token)
    with setup.sessions() as db:
        rows=calculate(db,DAY,DAY,now=datetime(2026,3,27))['items']
        assert {r['process_code'] for r in rows}=={'LT','TP'}
        by_id={r['key']:r for r in rows}
        assert by_id[str(equipment)]['total_qty']==331
        assert by_id[str(tap_id)]['total_qty']==331
        assert by_id[str(equipment)]['runtime_seconds']==12*3600
        assert by_id[str(tap_id)]['runtime_seconds']==620*60
    response=setup.client.get('/api/production/equipment/oee',params={'start_date':DAY.isoformat(),'end_date':DAY.isoformat()})
    assert response.status_code==200
    assert {r['process_code'] for r in response.json()['items']}=={'LT','TP'}
    assert setup.client.get('/api/production/equipment/oee',params={'start_date':DAY.isoformat(),'end_date':DAY.isoformat(),'process_code':'SER'}).status_code==422


def test_optional_exclusion_uses_equipment_item_and_day_and_preserves_original(setup):
    from models.production import ProductionWorkOrder
    order,_,_,equipment,_=prepare(setup)
    setup.app.include_router(router)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        other=add_item(db,part='NO-PLAN-A',code='LT')
        unknown_order=ProductionWorkOrder(work_order_no='UNPLANNED',order_date='2026-10-12',item_id=other,part_no='NO-PLAN-A',order_qty=100)
        db.add(unknown_order);db.flush()
        plan_ct(db,equipment,item,'2026-10-12',30)
        for order_id,day,shift in [(order,'2026-10-12','DAY'),(unknown_order.id,'2026-10-12','NIGHT'),(order,'2026-10-13','DAY')]:
            db.add(ProductionPerformance(work_order_id=order_id,performance_date=day,process_code='LT',equipment_id=equipment,shift_type=shift,good_qty=100))
        db.commit()
        original=calculate(db,date(2026,10,12),date(2026,10,13),now=datetime(2026,10,15))
        filtered=calculate(db,date(2026,10,12),date(2026,10,13),now=datetime(2026,10,15),exclude_unplanned=True)
        row=next(r for r in filtered['items'] if r['key']==str(equipment))
        assert original['summary']['total_qty']==300
        assert original['summary']['performance'] is None and original['summary']['oee'] is None
        assert original['summary']['runtime_seconds']==36*3600
        assert row['total_qty']==100 and row['runtime_seconds']==12*3600
        assert row['planned_seconds']==24*3600 and row['downtime_seconds']==12*3600
        assert row['excluded_records']==2 and row['excluded_total_qty']==200
        from models.models import ItemMasterModel
        assert set(row['excluded_parts'])=={'NO-PLAN-A',db.get(ItemMasterModel,item).part_no}
        assert filtered['summary']['performance']==pytest.approx(3000/(12*3600))
        assert filtered['summary']['oee']==pytest.approx(3000/(24*3600))
        assert db.query(ProductionPerformance).count()==3
    params={'start_date':'2026-10-12','end_date':'2026-10-13'}
    default=setup.client.get('/api/production/equipment/oee',params=params).json()
    assert default['exclude_unplanned'] is False and default['summary']['total_qty']==300
    selected=setup.client.get('/api/production/equipment/oee',params=params|{'exclude_unplanned':'true'}).json()
    assert selected['exclude_unplanned'] is True and selected['summary']['excluded_records']==2


def test_summary_aggregates_selected_process_with_weighted_ratios(setup):
    from models.production import ProductionWorkOrder
    order,_,_,equipment,_=prepare(setup)
    with setup.sessions() as db:
        item=db.get(ProductionWorkOrder,order).item_id
        tap=EquipmentMaster(equipment_code='SUMMARY-TP',equipment_name='탭핑 1호기',process_code='TP',machine_no='1');db.add(tap);db.flush()
        for equip,process,good,defect in [(equipment,'LT',100,0),(tap.id,'TP',100,100)]:
            plan_ct(db,equip,item,'2026-10-12',30)
            db.add(ProductionPerformance(work_order_id=order,performance_date='2026-10-12',process_code=process,equipment_id=equip,shift_type='DAY',good_qty=good,defect_qty=defect))
        db.commit()
        report=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,15))
        summary=report['summary'];base=(24+20.5)*3600;runtime=12*3600+620*60
        assert summary['name']=='전체 종합 (복합선반·탭핑)'
        assert summary['total_qty']==300 and summary['good_qty']==200
        assert summary['planned_seconds']==base and summary['runtime_seconds']==runtime
        assert summary['availability']==pytest.approx(runtime/base)
        assert summary['performance']==pytest.approx(300*30/runtime)
        assert summary['quality']==pytest.approx(2/3)
        assert summary['oee']==pytest.approx(200*30/base)
        lathe=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,15),process_code='LT')['summary']
        assert lathe['name']=='복합선반 종합' and lathe['total_qty']==100
        assert lathe['planned_seconds']==24*3600
