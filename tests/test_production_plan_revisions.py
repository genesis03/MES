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
from models.production import ProductionPerformance
from models.production_plan_revision import (ProductionPlanRevision,ProductionPlanRevisionEdit,
    ProductionPlanRevisionMigration,ProductionPlanRevisionReference)
from services.equipment_oee import calculate
from services.equipment_reports import daily_report,efficiency_report
from services.production_plan_revisions import plan_data

BASE='/api/production/month-plan'
PARAMS={'month':'2026-10','process_code':'LT'}


def meta(setup):return setup.client.get(BASE+'/revisions',params={'month':'2026-10'}).json()
def copy(setup,number):
    return setup.client.post(BASE+'/revisions/copy',json={'month':'2026-10','generation':meta(setup)['generation'],
        'number':number,'reason':'계획 개정'})
def apply(setup,number):
    return setup.client.post(BASE+'/revisions/apply',json={'month':'2026-10','generation':meta(setup)['generation'],
        'number':number,'reason':'계획 적용'})
def load(setup,number=None):
    return setup.client.get(BASE,params=PARAMS|({'revision':number} if number is not None else {})).json()
def edit(setup,number,body):
    return setup.client.put(BASE+f'/revisions/{number}',json=body|{'generation':meta(setup)['generation']})


def test_rev_zero_is_the_initial_editable_plan_and_updates_oee(setup,monkeypatch):
    order,item,equipment,_=initialize(setup,monkeypatch)
    assert load(setup)['view_revision']==0
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    assert len(meta(setup)['revisions'])==1 and 'dirty' not in meta(setup)
    assert load(setup)['lines'][0]['quantities']['2026-10-12']==100
    with setup.sessions() as db:
        db.add(ProductionPerformance(work_order_id=order,performance_date='2026-10-12',process_code='LT',
            equipment_id=equipment,shift_type='DAY',good_qty=90,defect_qty=10,setup_qty=0));db.commit()
        report=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        assert report['summary']['planned_qty']==100 and report['planned_seconds']==86400
        assert report['plan_revisions'][0]['number']==0
        audit=db.query(ProductionPlanRevisionEdit).one()
        assert json.loads(audit.before_json)['lines']==[] and audit.edited_by
    assert load(setup)['view_revision']==0


def test_copy_edit_and_apply_keep_other_revisions_and_frozen_reports_intact(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    with setup.sessions() as db:frozen=plan_data(db,date(2026,10,1),date(2026,10,31))
    for source in (0,1,2):
        response=copy(setup,source)
        assert response.status_code==200,response.text
        assert response.json()['created_revision']==source+1 and response.json()['applied_revision']==0
    original=load(setup,2)['lines']
    body=value(item,equipment,60)|{'work_times':[{'equipment_id':equipment,'work_date':'2026-10-12','start_time':'08:00','end_time':'20:00','next_day':False}]}
    body['lines'][0]['quantities']['2026-10-12']=500
    assert edit(setup,3,body).status_code==200
    assert load(setup,2)['lines']==original and load(setup)['view_revision']==0
    assert apply(setup,3).status_code==200
    assert load(setup)['view_revision']==3
    with setup.sessions() as db:
        result=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        assert result['summary']['planned_qty']==500 and result['planned_seconds']==43200
        assert result['plan_revisions'][0]['number']==3
        assert next(d.cycle_seconds for d,l in frozen.rows if d.work_date=='2026-10-12')==30
        assert daily_report(db,'2026-10',equipment,now=datetime(2026,10,20))['plan_revisions'][0]['number']==3
        assert efficiency_report(db,2026,10,now=datetime(2026,10,20))['plan_revisions'][0]['number']==3
    params={'start_date':'2026-10-01','end_date':'2026-10-31','revision':2}
    workbook=load_workbook(BytesIO(setup.client.get(BASE+'/excel',params=params).content))
    assert workbook.worksheets[1]['C1'].value=='2026-10 Rev.02'
    assert workbook.worksheets[1]['K3'].value==200
    assert setup.client.get(BASE+'/status',params=params|{'end_date':'2026-11-01'}).status_code==422
    assert apply(setup,0).status_code==200
    assert load(setup)['lines'][0]['cycle_seconds']==30


def test_same_revision_save_keeps_identity_and_audit_before_after(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    with setup.sessions() as db:
        revision=db.query(ProductionPlanRevision).one()
        identity=(revision.id,revision.number,revision.created_at,revision.created_by,revision.reason)
        before=revision.snapshot_json
    body=value(item,equipment,60)
    assert edit(setup,0,body).status_code==200
    with setup.sessions() as db:
        revision=db.query(ProductionPlanRevision).one()
        assert (revision.id,revision.number,revision.created_at,revision.created_by,revision.reason)==identity
        audit=db.query(ProductionPlanRevisionEdit).order_by(ProductionPlanRevisionEdit.id.desc()).first()
        assert audit.before_json==before and audit.after_json==revision.snapshot_json
    count=len(meta(setup)['edits'])
    assert edit(setup,0,body).status_code==200
    assert len(meta(setup)['edits'])==count


def test_conflicts_permissions_and_missing_revisions(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    generation=meta(setup)['generation']
    def save(quantity):
        body=value(item,equipment)|{'generation':generation}
        body['lines'][0]['quantities']['2026-10-12']=quantity
        return setup.client.put(BASE+'/revisions/0',json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(save,[101,202]))
    assert sorted(statuses)==[200,409]
    assert setup.client.post(BASE+'/revisions/copy',json={'month':'2026-10','generation':generation,'number':0,'reason':'stale'}).status_code==409
    assert copy(setup,99).status_code==404
    assert edit(setup,99,value(item,equipment)).status_code==404
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{'/production/plans':'READ'}}))
    assert load(setup)['can_write'] is False
    assert edit(setup,0,value(item,equipment)).status_code==403
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==403
    assert copy(setup,0).status_code==403 and apply(setup,0).status_code==403


def test_legacy_draft_is_preserved_once_without_changing_oee_selection(setup,monkeypatch):
    from models.production_month_plan import ProductionMonthPlanLine,ProductionMonthPlanDay
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    assert copy(setup,0).status_code==200
    with setup.sessions() as db:
        db.query(ProductionPlanRevisionMigration).delete()
        db.query(ProductionMonthPlanLine).one().cycle_seconds=75
        db.query(ProductionMonthPlanDay).filter_by(work_date='2026-10-12').one().quantity=777
        db.commit()
    converted=meta(setup)
    assert len(converted['revisions'])==3 and converted['applied_revision']==0
    assert load(setup,2)['lines'][0]['cycle_seconds']==75
    assert load(setup,2)['lines'][0]['quantities']['2026-10-12']==777
    assert load(setup)['lines'][0]['cycle_seconds']==30
    with setup.sessions() as db:assert db.query(ProductionMonthPlanLine).one().cycle_seconds==30
    assert meta(setup)['generation']==converted['generation'] and len(meta(setup)['revisions'])==3
    # Existing content equal to an older Rev is already preserved: no duplicate is made.
    with setup.sessions() as db:db.query(ProductionPlanRevisionMigration).delete();db.commit()
    assert len(meta(setup)['revisions'])==3


def test_same_number_change_during_report_loading_is_detected(setup,monkeypatch):
    from fastapi import HTTPException
    from services import production_plan_revisions as revisions
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    original=revisions.applied_snapshots;calls=0
    def changing(db,first,last):
        nonlocal calls
        data=original(db,first,last);calls+=1
        if calls==2:data['2026-10']['_snapshot_hash']='changed-content-with-same-revision-number'
        return data
    monkeypatch.setattr(revisions,'applied_snapshots',changing)
    with setup.sessions() as db:
        with pytest.raises(HTTPException) as error:plan_data(db,date(2026,10,1),date(2026,10,31))
        assert error.value.status_code==409


def test_revision_edit_keeps_other_process_and_deleted_row_audit_references(setup,monkeypatch):
    from copy import deepcopy
    from models.equipment import EquipmentMaster
    from services.production_plan_revisions import encoded
    _,item,equipment,other=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    copy(setup,0)
    with setup.sessions() as db:
        db.get(EquipmentMaster,other).process_code='TP'
        revision=db.query(ProductionPlanRevision).filter_by(number=1).one()
        data=json.loads(revision.snapshot_json)
        other_line=deepcopy(data['lines'][0]);other_line['equipment_id']=other
        other_times=[dict(t,equipment_id=other) for t in data['times']]
        data['lines'].append(other_line);data['times'].extend(other_times)
        revision.snapshot_json=encoded(data);db.commit();revision_id=revision.id
    assert edit(setup,1,value(item,equipment,40)).status_code==200
    with setup.sessions() as db:
        data=json.loads(db.get(ProductionPlanRevision,revision_id).snapshot_json)
        assert next(r for r in data['lines'] if r['equipment_id']==other)==other_line
        assert [r for r in data['times'] if r['equipment_id']==other]==other_times
    body=value(item,equipment)|{'lines':[],'deleted_line_keys':[[equipment,item+9999]]}
    assert edit(setup,1,body).status_code==422
    body['deleted_line_keys']=[[equipment,item]]
    assert edit(setup,1,body).status_code==200
    assert load(setup,1)['lines']==[] and len(load(setup)['lines'])==1
    with setup.sessions() as db:
        assert db.query(ProductionPlanRevisionReference).filter_by(revision_id=revision_id,item_id=item,equipment_id=equipment).count()==1


def test_default_status_and_excel_use_each_months_applied_revision(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    copy(setup,0)
    body=value(item,equipment);body['lines'][0]['quantities']['2026-10-12']=700
    assert edit(setup,1,body).status_code==200
    params={'start_date':'2026-10-01','end_date':'2026-11-01'}
    before=setup.client.get(BASE+'/status',params=params).json()
    assert before['total']==200 and 'Rev.00' in before['revision_context'] and '작업본' not in before['revision_context']
    assert apply(setup,1).status_code==200
    after=setup.client.get(BASE+'/status',params=params).json()
    assert after['total']==800 and 'Rev.01' in after['revision_context']


def test_direct_mutation_preserves_unmigrated_legacy_data_before_switching(setup,monkeypatch):
    from models.production_month_plan import ProductionMonthPlanLine
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    assert copy(setup,0).status_code==200
    generation=meta(setup)['generation']
    with setup.sessions() as db:
        db.query(ProductionPlanRevisionMigration).delete()
        db.query(ProductionMonthPlanLine).one().cycle_seconds=88
        db.commit()
    # An old client may submit Apply without loading the new page first.
    response=setup.client.post(BASE+'/revisions/apply',json={'month':'2026-10','generation':generation,'number':1,'reason':'전환'})
    assert response.status_code==409
    assert load(setup,2)['lines'][0]['cycle_seconds']==88
    assert meta(setup)['applied_revision']==0
    assert apply(setup,1).status_code==200
    assert load(setup,2)['lines'][0]['cycle_seconds']==88


def test_save_unifies_legacy_daily_ct_even_when_restoring_same_header_value(setup,monkeypatch):
    from services.production_plan_revisions import encoded
    _,item,equipment,_=initialize(setup,monkeypatch)
    body=value(item,equipment,30)
    assert setup.client.put(BASE,json=body).status_code==200
    # Reproduce an old date-based change: header was restored to 30 but some days stayed at 60.
    with setup.sessions() as db:
        revision=db.query(ProductionPlanRevision).one()
        data=json.loads(revision.snapshot_json)
        for day in data['lines'][0]['days']:
            if day['work_date']>='2026-10-12':day['cycle_seconds']=60
        revision.snapshot_json=encoded(data);db.commit()
    assert copy(setup,0).status_code==200
    original=load(setup,1)['lines']
    params={'start_date':'2026-10-01','end_date':'2026-10-31','revision':0}
    assert len(setup.client.get(BASE+'/status',params=params).json()['rows'])==2
    assert edit(setup,0,body).status_code==200
    report=setup.client.get(BASE+'/status',params=params).json()
    assert len(report['rows'])==1 and report['rows'][0]['cycle_seconds']==30 and report['total']==200
    assert load(setup,0)['lines'][0]['cycle_history']==[30]
    assert load(setup,1)['lines']==original
    with setup.sessions() as db:
        result=calculate(db,date(2026,10,12),date(2026,10,12),now=datetime(2026,10,20))
        assert next(r for r in result['items'] if r['key']==str(equipment))['planned_qty']==100
    workbook=load_workbook(BytesIO(setup.client.get(BASE+'/excel',params=params).content))
    assert workbook.worksheets[1]['K3'].value==200
