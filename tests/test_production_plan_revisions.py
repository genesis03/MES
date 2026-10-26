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


def test_direct_rev_zero_edit_preserves_number_draft_and_audit(setup,monkeypatch):
    from test_equipment_oee import plan_ct
    from models.production_plan_revision import ProductionPlanRevisionEdit
    from models.production_month_plan import ProductionMonthPlanLine
    _,item,equipment,_=initialize(setup,monkeypatch)
    with setup.sessions() as db:
        plan_ct(db,equipment,item,'2026-10-12',30);db.commit()
    initial=meta(setup)
    with setup.sessions() as db:
        revision=db.query(ProductionPlanRevision).one()
        identity=(revision.id,revision.created_at,revision.created_by,revision.reason)
        before=revision.snapshot_json
        frozen=plan_data(db,date(2026,10,1),date(2026,10,31))
    body=value(item,equipment,60)|{'generation':initial['generation'],'ct_effective_date':'2026-10-01',
        'work_times':[{'equipment_id':equipment,'work_date':'2026-10-12','start_time':'08:00','end_time':'20:00','next_day':False}]}
    body['lines'][0]['quantities']['2026-10-12']=500
    response=setup.client.put(BASE+'/revisions/0',json=body)
    assert response.status_code==200,response.text
    assert response.json()['applied_revision']==0
    assert len(meta(setup)['revisions'])==1
    with setup.sessions() as db:
        revision=db.query(ProductionPlanRevision).one()
        assert (revision.id,revision.created_at,revision.created_by,revision.reason)==identity
        assert db.query(ProductionMonthPlanLine).one().cycle_seconds==30
        edit=db.query(ProductionPlanRevisionEdit).one()
        assert edit.before_json==before and edit.after_json==revision.snapshot_json and edit.edited_by
        current=plan_data(db,date(2026,10,1),date(2026,10,31))
        assert next(d.quantity for d,l in current.rows if d.work_date=='2026-10-12')==500
        assert next(d.cycle_seconds for d,l in current.rows if d.work_date=='2026-10-12')==60
        assert next(d.cycle_seconds for d,l in frozen.rows if d.work_date=='2026-10-12')==30
        oee=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        assert oee['summary']['planned_qty']==500 and oee['planned_seconds']==43200
        assert oee['plan_revisions'][0]['number']==0
    assert len(meta(setup)['edits'])==1
    assert setup.client.put(BASE+'/revisions/0',json=body).status_code==409
    body['generation']=meta(setup)['generation']
    assert setup.client.put(BASE+'/revisions/0',json=body).status_code==200
    assert len(meta(setup)['edits'])==1
    assert setup.client.put(BASE+'/revisions/99',json=body).status_code==404
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{'/production/plans':'READ'}}))
    assert setup.client.put(BASE+'/revisions/0',json=body).status_code==403


def test_edit_unapplied_revision_keeps_active_plan_and_validates_deletion(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    assert publish(setup)['applied_revision']==1
    body=value(item,equipment,75)|{'generation':meta(setup)['generation']}
    response=setup.client.put(BASE+'/revisions/0',json=body)
    assert response.status_code==200,response.text
    assert meta(setup)['applied_revision']==1
    with setup.sessions() as db:
        active=plan_data(db,date(2026,10,1),date(2026,10,31))
        assert next(d.cycle_seconds for d,l in active.rows if d.work_date=='2026-10-12')==30
    body['generation']=meta(setup)['generation']
    body['deleted_line_keys']=[[equipment,item+9999]]
    assert setup.client.put(BASE+'/revisions/0',json=body).status_code==422
    body['deleted_line_keys']=[[equipment,item]];body['lines']=[]
    assert setup.client.put(BASE+'/revisions/0',json=body).status_code==200
    params={'month':'2026-10','process_code':'LT'}
    assert setup.client.get(BASE,params=params|{'revision':0}).json()['lines']==[]
    assert len(setup.client.get(BASE,params=params|{'revision':1}).json()['lines'])==1
    assert len(setup.client.get(BASE,params=params).json()['lines'])==1


def test_same_number_change_during_report_loading_is_detected(setup,monkeypatch):
    from fastapi import HTTPException
    from services import production_plan_revisions as revisions
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    publish(setup)
    original=revisions.applied_snapshots
    calls=0
    def changing(db,first,last):
        nonlocal calls
        data=original(db,first,last);calls+=1
        if calls==2:data['2026-10']['_snapshot_hash']='changed-content-with-same-revision-number'
        return data
    monkeypatch.setattr(revisions,'applied_snapshots',changing)
    with setup.sessions() as db:
        with pytest.raises(HTTPException) as error:plan_data(db,date(2026,10,1),date(2026,10,31))
        assert error.value.status_code==409


def test_revision_edit_keeps_other_process_content_and_old_references(setup,monkeypatch):
    from copy import deepcopy
    from models.equipment import EquipmentMaster
    from models.production_plan_revision import ProductionPlanRevisionReference
    from services.production_plan_revisions import encoded
    _,item,equipment,other=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    publish(setup)
    with setup.sessions() as db:
        db.get(EquipmentMaster,other).process_code='TP'
        revision=db.query(ProductionPlanRevision).filter_by(number=1).one()
        data=json.loads(revision.snapshot_json)
        other_line=deepcopy(data['lines'][0]);other_line['equipment_id']=other
        other_times=[dict(t,equipment_id=other) for t in data['times']]
        data['lines'].append(other_line);data['times'].extend(other_times)
        revision.snapshot_json=encoded(data);db.commit()
        revision_id=revision.id
    body=value(item,equipment,40)|{'generation':meta(setup)['generation']}
    assert setup.client.put(BASE+'/revisions/1',json=body).status_code==200
    with setup.sessions() as db:
        data=json.loads(db.get(ProductionPlanRevision,revision_id).snapshot_json)
        assert next(r for r in data['lines'] if r['equipment_id']==other)==other_line
        assert [r for r in data['times'] if r['equipment_id']==other]==other_times
    body.update(generation=meta(setup)['generation'],lines=[],deleted_line_keys=[[equipment,item]])
    assert setup.client.put(BASE+'/revisions/1',json=body).status_code==200
    with setup.sessions() as db:
        assert db.query(ProductionPlanRevisionReference).filter_by(revision_id=revision_id,item_id=item,equipment_id=equipment).count()==1


def test_copy_selected_revision_creates_next_number_without_applying(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    publish(setup)
    def copy(number):
        return setup.client.post(BASE+'/revisions/copy',json={'month':'2026-10','generation':meta(setup)['generation'],'number':number,'reason':'선택 계획 복사'})
    response=copy(1)
    assert response.status_code==200,response.text
    assert response.json()['created_revision']==2 and response.json()['applied_revision']==1
    params={'month':'2026-10','process_code':'LT'}
    before=setup.client.get(BASE,params=params|{'revision':2}).json()['lines']
    response=copy(2)
    assert response.status_code==200 and response.json()['created_revision']==3
    assert setup.client.get(BASE,params=params|{'revision':3}).json()['lines']==before
    body=value(item,equipment,65)|{'generation':meta(setup)['generation'],'ct_effective_date':'2026-10-01'}
    assert setup.client.put(BASE+'/revisions/3',json=body).status_code==200
    assert setup.client.get(BASE,params=params|{'revision':2}).json()['lines']==before
    assert meta(setup)['applied_revision']==1
    assert setup.client.get(BASE,params=params).json()['lines'][0]['cycle_seconds']==30
    stale=body['generation']
    assert setup.client.post(BASE+'/revisions/copy',json={'month':'2026-10','generation':stale,'number':2,'reason':'stale'}).status_code==409
    response=copy(0)
    assert response.json()['created_revision']==4
    assert setup.client.get(BASE,params=params|{'revision':4}).json()['lines']==[]
    assert copy(99).status_code==404
    response=copy(None)
    assert response.status_code==200 and response.json()['created_revision']==5
    assert setup.client.get(BASE,params=params|{'revision':5}).json()['lines'][0]['cycle_seconds']==30
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{'/production/plans':'READ'}}))
    assert copy(2).status_code==403
