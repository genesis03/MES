from datetime import datetime,date
from test_production_sync import setup
from test_production_run_registration import prepare
from routers.production_month_plan import router
from routers import production_month_plan as api
from routers.equipment_oee import router as oee_router
from models.production import ProductionWorkOrder,ProductionPerformance
from models.production_cycle_time import ProductionCycleTime
from models.production_month_plan import ProductionMonthPlanDay,ProductionEquipmentSchedule
from services.equipment_oee import calculate


def initialize(setup,monkeypatch):
    order,_,worker,equipment,other=prepare(setup)
    setup.app.include_router(router);setup.app.include_router(oee_router)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,10,12,12)
    monkeypatch.setattr(api,'datetime',FixedDatetime)
    with setup.sessions() as db:item=db.get(ProductionWorkOrder,order).item_id
    return order,item,equipment,other


def value(item,equipment,ct=30):
    return dict(month='2026-10',process_code='LT',ct_effective_date='2026-10-12',lines=[dict(item_id=item,equipment_id=equipment,cycle_seconds=ct,expected_oee=.95,quantities={f'2026-10-{i:02d}':100 if i in (6,12) else 0 for i in range(1,32)})])


def test_plan_ct_is_date_scoped_and_master_changes_do_not_rewrite_oee(setup,monkeypatch):
    order,item,equipment,_=initialize(setup,monkeypatch)
    response=setup.client.put('/api/production/month-plan',json=value(item,equipment));assert response.status_code==200,response.text
    response=setup.client.put('/api/production/month-plan',json=value(item,equipment,40));assert response.status_code==200,response.text
    with setup.sessions() as db:
        rows={d.work_date:d for d in db.query(ProductionMonthPlanDay)}
        assert rows['2026-10-06'].cycle_seconds==30
        assert rows['2026-10-12'].cycle_seconds==40
        db.add(ProductionCycleTime(item_id=item,seconds=80))
        db.add(ProductionPerformance(work_order_id=order,performance_date='2026-10-06',process_code='LT',shift_type='DAY',equipment_id=equipment,good_qty=100,defect_qty=0,setup_qty=0));db.commit()
        before=calculate(db,date(2026,10,6),date(2026,10,6),now=datetime(2026,10,20))
        db.get(ProductionCycleTime,item).seconds=99;db.commit()
        after=calculate(db,date(2026,10,6),date(2026,10,6),now=datetime(2026,10,20))
        target=lambda r:next(g for g in r['items'] if g['key']==str(equipment))
        assert target(before)['oee'] is not None
        assert target(before)['oee']==target(after)['oee']
        assert target(after)['parts'][0]['cycle_seconds']==30
    # Quantity-only save must not overwrite earlier C/T even if an earlier date is supplied.
    same=value(item,equipment,40)|{'ct_effective_date':'2026-10-01'}
    assert setup.client.put('/api/production/month-plan',json=same).status_code==200
    bad=value(item,equipment,50)|{'ct_effective_date':'2026-10-01'}
    assert setup.client.put('/api/production/month-plan',json=bad).status_code==422
    with setup.sessions() as db:assert db.query(ProductionMonthPlanDay).filter_by(work_date='2026-10-06').one().cycle_seconds==30
    report=setup.client.get('/api/production/month-plan/status',params={'start_date':'2026-10-01','end_date':'2026-10-31'}).json()
    assert report['total']==200 and len(report['rows'])==2
    assert {r['cycle_seconds'] for r in report['rows']}=={30,40}
    assert sum(r['total'] for r in report['rows'])==200


def test_equipment_schedules_are_independent_and_reflected_in_oee(setup,monkeypatch):
    _,item,equipment,other=initialize(setup,monkeypatch)
    query={'month':'2026-10','process_code':'LT','equipment_id':equipment}
    rows=setup.client.get('/api/production/equipment/schedule',params=query).json()['items']
    for row in rows:row['working']=False
    response=setup.client.put('/api/production/equipment/schedule',json=query|{'items':rows});assert response.status_code==200,response.text
    with setup.sessions() as db:
        result=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        assert next(r for r in result['items'] if r['key']==str(equipment))['planned_seconds']==0
        assert next(r for r in result['items'] if r['key']==str(other))['planned_seconds']==24*3600
        assert db.query(ProductionEquipmentSchedule).count()==31
    wrong=setup.client.get('/api/production/equipment/schedule',params=query|{'process_code':'TP'})
    assert wrong.status_code==422


def test_duplicate_plan_rows_and_invalid_quantities_are_atomic(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    payload=value(item,equipment);payload['lines']*=2
    assert setup.client.put('/api/production/month-plan',json=payload).status_code==422
    payload=value(item,equipment);payload['lines'][0]['quantities']['2026-10-01']=-1
    assert setup.client.put('/api/production/month-plan',json=payload).status_code==422
    with setup.sessions() as db:assert db.query(ProductionMonthPlanDay).count()==0
    assert setup.client.get('/api/production/equipment/oee',params={'start_date':'2026-10-01','end_date':'2026-10-31','process_code':'DOT'}).status_code==422


def test_plan_references_are_reported_in_item_usage_and_block_equipment_deletion(setup,monkeypatch):
    from services.item_identity_service import item_usage_summary
    from routers import basic_info_equipment
    from test_production_sync import ADMIN
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put('/api/production/month-plan',json=value(item,equipment)).status_code==200
    with setup.sessions() as db:
        assert any(row['table']=='production_month_plan_lines' for row in item_usage_summary(db,item))
    setup.app.include_router(basic_info_equipment.router)
    monkeypatch.setattr(basic_info_equipment,'get_current_user_optional',lambda request,db:ADMIN)
    assert setup.client.delete('/api/basic-info/equipment/'+str(equipment)).status_code==409
