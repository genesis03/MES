from datetime import date,datetime
import pytest
from test_production_sync import setup,ADMIN
from test_production_run_registration import prepare
from routers.equipment_oee import router
from services.equipment_oee import calculate
from models.oee_schedule import OeeScheduleDay
from models.production_cycle_time import ProductionCycleTime
from models.models import ItemMasterModel


def setup_oee(setup):
    prepare(setup);setup.app.include_router(router)


def monthly(setup,month='2026-10',process='LT'):
    result=setup.client.get('/api/production/equipment/schedule',params={'month':month,'process_code':process})
    assert result.status_code==200,result.text
    return result.json()['items']


def payload(rows,month='2026-10',process='LT'):
    return dict(month=month,process_code=process,items=[{key:r[key] for key in ['work_date','working','start_time','end_time','next_day']} for r in rows])


def test_calendar_is_readonly_and_plan_quantities_enable_holiday_work(setup):
    from routers.production_month_plan import router as plans
    from models.production import ProductionWorkOrder
    setup_oee(setup);setup.app.include_router(plans)
    with setup.sessions() as db:
        order=db.query(ProductionWorkOrder).first();item=order.item_id
        from models.equipment import EquipmentMaster
        equipment=db.query(EquipmentMaster).filter_by(process_code='LT',machine_no='1').one().id
    rows=monthly(setup)
    assert len(rows)==31 and not any(r['working'] for r in rows)
    assert setup.client.put('/api/production/equipment/schedule',json=payload(rows)).status_code==409
    plan={'month':'2026-10','process_code':'LT','ct_effective_date':'2026-10-12','lines':[{'equipment_id':equipment,'item_id':item,'cycle_seconds':30,'expected_oee':.95,'quantities':{f'2026-10-{i:02d}':100 if i in (9,10) else 0 for i in range(1,32)}}]}
    assert setup.client.put('/api/production/month-plan',json=plan).status_code==200
    by_date={r['work_date']:r for r in monthly(setup)}
    assert by_date['2026-10-09']['working'] and by_date['2026-10-10']['working']
    with setup.sessions() as db:
        report=calculate(db,date(2026,10,9),date(2026,10,10),now=datetime(2026,10,20),process_code='LT')
        assert report['planned_seconds']==48*3600


def test_invalid_month_and_nonmanaged_process_rejected(setup):
    setup_oee(setup)
    assert setup.client.get('/api/production/equipment/schedule',params={'month':'2026-13','process_code':'LT'}).status_code==422
    assert setup.client.get('/api/production/equipment/schedule',params={'month':'2026-10','process_code':'ASSY'}).status_code==422
    assert {p['process_code'] for p in setup.client.get('/api/production/equipment/oee/processes').json()['items']}=={'LT','TP'}


def test_tapping_scope_and_full_detail_shift_affect_oee(setup):
    from models.equipment import EquipmentMaster
    from models.production import ProductionPerformance,ProductionWorkOrder
    setup_oee(setup)
    with setup.sessions() as db:
        item=ItemMasterModel(part_no='TAP-B',part_name='Tap item',account_type='SEMI',material_type='SEMI',production_loc='TP',created_at='2026-01-01')
        db.add(item);db.flush()
        order=ProductionWorkOrder(work_order_no='WTAP',order_date='2026-10-12',item_id=item.id,part_no=item.part_no,order_qty=1000)
        equipment=EquipmentMaster(equipment_code='TP1',equipment_name='탭핑 1호기',process_code='TP',machine_no='1')
        db.add_all([order,equipment]);db.flush()
        from test_equipment_oee import plan_ct
        plan_ct(db,equipment.id,item.id,'2026-10-12',30)
        db.add(ProductionPerformance(work_order_id=order.id,performance_date='2026-10-12',process_code='TP',shift_type='DAY',equipment_id=equipment.id,good_qty=900,defect_qty=100,setup_qty=0));db.commit()
        result=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20),process_code='TP')
        assert len(result['items'])==1 and result['planned_seconds']==pytest.approx(24*3600-210*60)
        assert result['items'][0]['oee']==pytest.approx(900*30/(24*3600-210*60))
        assert calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20),process_code='LT')['planned_seconds']==0


def test_item_master_cycle_roundtrip_clear_and_transaction_rollback(setup,monkeypatch):
    from routers import basic_info
    setup_oee(setup);setup.app.include_router(basic_info.router)
    monkeypatch.setattr(basic_info,'get_current_user_optional',lambda request,db:ADMIN)
    data=dict(part_no='CYCLE-A',part_name='New cycle item',account_type='PROD',material_type='SEMI',production_loc='LT',cycle_seconds=42)
    result=setup.client.post('/api/basic-info/items/create',json=data)
    assert result.status_code==200,result.text
    item_id=result.json()['id']
    result=setup.client.get('/api/basic-info/items')
    assert result.status_code==200,result.text
    item=next(i for i in result.json()['data'] if i['id']==item_id)
    assert item['cycle_seconds']==42
    assert setup.client.post('/api/basic-info/items/update',json=data|{'id':item_id,'cycle_seconds':0}).status_code==422
    with setup.sessions() as db:assert db.get(ProductionCycleTime,item_id).seconds==42
    # Omitting the field from an older client preserves the existing cycle.
    older={k:v for k,v in data.items() if k!='cycle_seconds'}
    assert setup.client.post('/api/basic-info/items/update',json=older|{'id':item_id}).status_code==200
    with setup.sessions() as db:assert db.get(ProductionCycleTime,item_id).seconds==42
    assert setup.client.post('/api/basic-info/items/update',json=data|{'id':item_id,'cycle_seconds':None}).status_code==200
    with setup.sessions() as db:assert db.get(ProductionCycleTime,item_id) is None
    assert setup.client.post('/api/basic-info/items/update',json=data|{'id':item_id}).status_code==200
    assert setup.client.post('/api/basic-info/items/delete',json={'id':item_id}).status_code==200
    with setup.sessions() as db:assert db.get(ProductionCycleTime,item_id) is None
    assert setup.client.post('/api/basic-info/items/create',json=data|{'part_no':'INVALID-A','cycle_seconds':-2}).status_code==422
    with setup.sessions() as db:assert db.query(ItemMasterModel).filter_by(part_no='INVALID-A').count()==0


def test_schedule_readonly_permission(setup):
    from types import SimpleNamespace
    import json
    from core.security import get_current_user
    setup_oee(setup)
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='readonly',role='USER',permissions=json.dumps({'menu_access':{'/production/plans':'READ'}}))
    rows=monthly(setup)
    assert setup.client.put('/api/production/equipment/schedule',json=payload(rows)).status_code==403
