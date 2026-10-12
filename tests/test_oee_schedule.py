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


def test_month_defaults_and_save_manual_holiday_operation(setup):
    setup_oee(setup);rows=monthly(setup)
    assert len(rows)==31
    by_day={r['work_date']:r for r in rows}
    assert by_day['2026-10-12']['start_time']=='08:00'
    assert by_day['2026-10-12']['end_time']=='08:00' and by_day['2026-10-12']['next_day']
    assert by_day['2026-10-16']['end_time']=='05:00'
    assert not by_day['2026-10-09']['working'] and not by_day['2026-10-17']['working']
    for row in rows:row['working']=row['work_date']=='2026-10-09'
    result=setup.client.put('/api/production/equipment/schedule',json=payload(rows));assert result.status_code==200,result.text
    assert next(r for r in monthly(setup) if r['work_date']=='2026-10-09')['working']
    # Explicit holiday operation overrides the initial public-holiday default.
    with setup.sessions() as db:
        report=calculate(db,date(2026,10,9),date(2026,10,10),now=datetime(2026,10,20),process_code='LT')
        assert report['planned_seconds']==21*3600
        assert db.query(OeeScheduleDay).filter_by(process_code='TP').count()==0


def test_overlap_missing_dates_and_nonmachining_process_rejected(setup):
    setup_oee(setup);rows=monthly(setup)
    next(r for r in rows if r['work_date']=='2026-10-12')['end_time']='10:00'
    assert setup.client.put('/api/production/equipment/schedule',json=payload(rows)).status_code==422
    assert setup.client.put('/api/production/equipment/schedule',json=payload(rows[:-1])).status_code==422
    september=monthly(setup,'2026-09')
    next(r for r in september if r['work_date']=='2026-09-30')['end_time']='10:00'
    assert setup.client.put('/api/production/equipment/schedule',json=payload(september,'2026-09')).status_code==422
    assert setup.client.get('/api/production/equipment/schedule',params={'month':'2026-13','process_code':'LT'}).status_code==422
    assert setup.client.get('/api/production/equipment/schedule',params={'month':'2026-10','process_code':'ASSY'}).status_code==422
    assert {p['process_code'] for p in setup.client.get('/api/production/equipment/oee/processes').json()['items']}=={'LT','TP'}
    with setup.sessions() as db:assert db.query(OeeScheduleDay).count()==0


def test_tapping_scope_and_full_shift_dates_affect_oee(setup):
    from models.equipment import EquipmentMaster
    from models.production import ProductionPerformance,ProductionWorkOrder
    setup_oee(setup);rows=monthly(setup,process='TP')
    for row in rows:
        row['working']=row['work_date']=='2026-10-12'
        if row['working']:row.update(end_time='20:00',next_day=False)
    assert setup.client.put('/api/production/equipment/schedule',json=payload(rows,process='TP')).status_code==200
    with setup.sessions() as db:
        item=ItemMasterModel(part_no='TAP-B',part_name='Tap item',account_type='SEMI',material_type='SEMI',production_loc='TP',created_at='2026-01-01')
        db.add(item);db.flush()
        db.add(ProductionCycleTime(item_id=item.id,seconds=30))
        order=ProductionWorkOrder(work_order_no='WTAP',order_date='2026-10-12',item_id=item.id,part_no=item.part_no,order_qty=1000)
        equipment=EquipmentMaster(equipment_code='TP1',equipment_name='탭핑 1호기',process_code='TP',machine_no='1')
        db.add_all([order,equipment]);db.flush()
        from test_equipment_oee import plan_ct
        plan_ct(db,equipment.id,item.id,'2026-10-12',30)
        db.add(ProductionPerformance(work_order_id=order.id,performance_date='2026-10-12',process_code='TP',shift_type='DAY',equipment_id=equipment.id,good_qty=900,defect_qty=100,setup_qty=0));db.commit()
        result=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20),process_code='TP')
        assert len(result['items'])==1
        assert result['planned_seconds']==12*3600
        assert result['items'][0]['oee']==pytest.approx(900*30/(12*3600))
        # A production date counts its full shift, including next day's end time.
        result=calculate(db,date(2026,10,1),date(2026,10,1),now=datetime(2026,10,20),process_code='LT')
        assert result['planned_seconds']==24*3600
        monday=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20),process_code='LT')
        assert monday['planned_seconds']==24*3600
        friday=calculate(db,date(2026,10,16),date(2026,10,16),now=datetime(2026,10,20),process_code='LT')
        assert friday['planned_seconds']==21*3600


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
