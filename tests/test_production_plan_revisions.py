import json
from datetime import date,datetime
from io import BytesIO
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from openpyxl import load_workbook
from test_production_sync import setup
from test_production_month_plan import initialize,value
from core.security import get_current_user
from models.production import ProductionWorkOrder,ProductionPerformance
from models.production_plan_revision import ProductionPlanRevision,ProductionPlanRevisionState
from services.equipment_oee import calculate
from services.equipment_reports import daily_report,efficiency_report
from services.production_plan_revisions import plan_data

BASE='/api/production/month-plan'


def meta(setup):return setup.client.get(BASE+'/revisions',params={'month':'2026-10'}).json()
def publish(setup):
    response=setup.client.post(BASE+'/revisions',json={'month':'2026-10','generation':meta(setup)['generation'],'reason':'생산계획 개정'})
    assert response.status_code==200,response.text
    return response.json()


def test_draft_isolated_revision_selection_and_immutable_hours_ct_quantities(setup,monkeypatch):
    order,item,equipment,_=initialize(setup,monkeypatch)
    first=value(item,equipment)
    assert setup.client.put(BASE,json=first).status_code==200
    with setup.sessions() as db:
        db.add(ProductionPerformance(work_order_id=order,performance_date='2026-10-12',process_code='LT',
            shift_type='DAY',equipment_id=equipment,good_qty=90,defect_qty=10,setup_qty=0));db.commit()
        assert calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))['planned_seconds']==0
    assert publish(setup)['applied_revision']==1
    revised=value(item,equipment,60)|{'ct_effective_date':'2026-10-01','generation':meta(setup)['generation'],
        'work_times':[{'equipment_id':equipment,'work_date':'2026-10-12','start_time':'08:00','end_time':'20:00','next_day':False}]}
    revised['lines'][0]['quantities']['2026-10-12']=500
    assert setup.client.put(BASE,json=revised).status_code==200
    saved=meta(setup)
    assert saved['dirty'] and len(saved['revisions'])==2
    with setup.sessions() as db:
        before=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        row=next(r for r in before['items'] if r['key']==str(equipment))
        assert row['planned_qty']==100 and row['parts'][0]['cycle_seconds']==30 and row['planned_seconds']==86400
        daily=daily_report(db,'2026-10',equipment,now=datetime(2026,10,20))
        assert daily['days'][11]['cycles'][0]['cycle_seconds']==30
        assert daily['plan_revisions'][0]['number']==1
        frozen=plan_data(db,date(2026,10,1),date(2026,10,31))
    for number,quantity,hours in [(None,500,False),(1,100,True)]:
        params={'month':'2026-10','process_code':'LT'}
        if number is not None:params['revision']=number
        loaded=setup.client.get(BASE,params=params).json()
        assert loaded['lines'][0]['quantities']['2026-10-12']==quantity
        assert next(d for d in loaded['schedules'][str(equipment)] if d['work_date']=='2026-10-12')['next_day']==hours
    assert publish(setup)['applied_revision']==2
    with setup.sessions() as db:
        current=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        row=next(r for r in current['items'] if r['key']==str(equipment))
        assert row['planned_qty']==500 and row['parts'][0]['cycle_seconds']==60 and row['planned_seconds']==43200
        assert current['plan_revisions'][0]['number']==2
        # A captured report context remains internally consistent after another apply.
        old=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20),plan_context=frozen)
        assert old['summary']['planned_qty']==100 and old['plan_revisions'][0]['number']==1
    response=setup.client.post(BASE+'/revisions/apply',json={'month':'2026-10','generation':meta(setup)['generation'],'number':1,'reason':'이전 기준 적용'})
    assert response.status_code==200
    assert response.json()['applied_revision']==1 and response.json()['dirty']
    assert setup.client.get(BASE,params={'month':'2026-10','process_code':'LT'}).json()['lines'][0]['quantities']['2026-10-12']==500
    with setup.sessions() as db:
        annual=efficiency_report(db,2026,10,now=datetime(2026,10,20))
        assert annual['plan_revisions'][0]['number']==1
        assert annual['months'][9]['summary']['planned_qty']==200
        old=db.query(ProductionPlanRevision).filter_by(month='2026-10',number=1).one()
        assert json.loads(old.snapshot_json)['lines'][0]['days'][11]['quantity']==100
    params={'start_date':'2026-10-01','end_date':'2026-10-31','revision':1}
    workbook=load_workbook(BytesIO(setup.client.get(BASE+'/excel',params=params).content))
    assert workbook.worksheets[1]['C1'].value=='2026-10 Rev.01'
    assert workbook.worksheets[1]['K3'].value==200
    assert setup.client.get(BASE+'/status',params=params|{'end_date':'2026-11-01'}).status_code==422


def test_conflicts_permissions_and_revision_numbers_are_month_scoped(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    payload=value(item,equipment)
    loaded=setup.client.get(BASE,params={'month':'2026-10','process_code':'LT'}).json()
    generation=loaded['revision']['generation']
    def save(number):
        body=value(item,equipment)|{'generation':generation}
        body['lines'][0]['quantities']['2026-10-12']=number
        return setup.client.put(BASE,json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(save,[101,202]))
    assert sorted(statuses)==[200,409]
    assert setup.client.post(BASE+'/revisions',json={'month':'2026-10','generation':generation,'reason':'stale'}).status_code==409
    assert publish(setup)['applied_revision']==1
    assert setup.client.post(BASE+'/revisions',json={'month':'2026-10','generation':meta(setup)['generation'],'reason':'same'}).status_code==409
    assert setup.client.get(BASE,params={'month':'2026-10','process_code':'LT','revision':99}).status_code==404
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{'/production/plans':'READ'}}))
    assert setup.client.get(BASE,params={'month':'2026-10','process_code':'LT'}).json()['can_write'] is False
    assert setup.client.put(BASE,json=payload).status_code==403
    for path,extra in [('/revisions',{}),('/revisions/apply',{'number':0})]:
        assert setup.client.post(BASE+path,json={'month':'2026-10','generation':0,'reason':'denied'}|extra).status_code==403


def test_rev_zero_preserves_existing_plan_and_revision_references_protect_items(setup,monkeypatch):
    from test_equipment_oee import plan_ct
    from services.item_identity_service import item_usage_summary
    from models.production_month_plan import ProductionMonthPlanLine
    _,item,equipment,_=initialize(setup,monkeypatch)
    with setup.sessions() as db:plan_ct(db,equipment,item,'2026-10-12',45);db.commit()
    loaded=setup.client.get(BASE,params={'month':'2026-10','process_code':'LT'}).json()
    assert loaded['revision']['applied_revision']==0 and not loaded['revision']['dirty']
    assert setup.client.put(BASE,json=value(item,equipment,60)|{'ct_effective_date':'2026-10-01'}).status_code==200
    old=setup.client.get(BASE,params={'month':'2026-10','process_code':'LT','revision':0}).json()
    assert old['lines'][0]['cycle_seconds']==45
    with setup.sessions() as db:
        assert any(r['table']=='production_plan_revision_references' for r in item_usage_summary(db,item))
        assert db.query(ProductionMonthPlanLine).one().cycle_seconds==60
        before=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        from services import oee_schedule
        monkeypatch.setattr(oee_schedule,'default_excluded_minutes',lambda _:60)
        after=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        assert before['planned_seconds']==after['planned_seconds']==86400
