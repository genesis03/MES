import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from test_production_sync import setup
from test_production_month_plan import initialize,value
from core.security import get_current_user
from models.production_plan_revision import (ProductionPlanRevision,ProductionPlanRevisionState,
    ProductionPlanRevisionEdit,ProductionPlanRevisionReference,ProductionPlanRevisionApplication,
    ProductionPlanRevisionDeletion)
from services.production_plan_revisions import ensure,add_revision,capture

BASE='/api/production/month-plan'


def meta(setup):return setup.client.get(BASE+'/revisions',params={'month':'2026-10'}).json()
def action(setup,path,**extra):
    return setup.client.post(BASE+'/revisions/'+path,json={'month':'2026-10',
        'generation':meta(setup)['generation'],'reason':'검증용 개정'}|extra)
def delete(setup,number,**extra):
    return setup.client.request('DELETE',BASE+f'/revisions/{number}',json={'month':'2026-10',
        'generation':meta(setup)['generation'],'reason':'잘못 만든 개정 삭제'}|extra)


def test_delete_archives_full_revision_and_history_without_touching_other_plans(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    assert setup.client.put(BASE,json=value(item,equipment)).status_code==200
    assert action(setup,'copy',number=0).status_code==200
    assert setup.client.put(BASE+'/revisions/1',json=value(item,equipment,60)|{'generation':meta(setup)['generation']}).status_code==200
    assert action(setup,'apply',number=1).status_code==200
    assert action(setup,'apply',number=0).status_code==200
    with setup.sessions() as db:
        row=db.query(ProductionPlanRevision).filter_by(month='2026-10',number=1).one()
        original=row.snapshot_json;revision_id=row.id
        before=db.query(ProductionPlanRevision).filter_by(month='2026-10',number=0).one().snapshot_json
        ensure(db,'2026-11');other=add_revision(db,'2026-11',1,capture(db,'2026-11'),'다른 월 개정','admin');db.commit();other_id=other.id
    generation=meta(setup)['generation']
    response=delete(setup,1)
    assert response.status_code==200,response.text
    assert response.json()['deleted_revision']==1 and response.json()['applied_revision']==0
    assert response.json()['generation']==generation+1
    assert [r['number'] for r in response.json()['revisions']]==[0]
    assert response.json()['deletions'][0]['reason']=='잘못 만든 개정 삭제'
    with setup.sessions() as db:
        assert db.get(ProductionPlanRevision,revision_id) is None
        assert db.get(ProductionPlanRevision,other_id) is not None
        assert db.query(ProductionPlanRevision).filter_by(month='2026-10',number=0).one().snapshot_json==before
        archive=db.query(ProductionPlanRevisionDeletion).one()
        assert archive.month=='2026-10' and archive.number==1 and archive.source_revision_id==revision_id
        assert archive.deleted_by and archive.deleted_at
        payload=json.loads(archive.payload_json)
        assert payload['revision']['snapshot_json']==original
        assert payload['edits'] and payload['applications'] and payload['references']
        for model in (ProductionPlanRevisionEdit,ProductionPlanRevisionReference,ProductionPlanRevisionApplication):
            assert db.query(model).filter_by(revision_id=revision_id).count()==0
    assert delete(setup,1).status_code==404
    # Removing the final numbered revision lets the next copy use Rev.01 again.
    assert action(setup,'copy',number=0).json()['created_revision']==1
    with setup.sessions() as db:assert db.query(ProductionPlanRevisionDeletion).count()==1


def test_rev_zero_and_current_applied_revision_cannot_be_deleted(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    setup.client.put(BASE,json=value(item,equipment))
    generation=meta(setup)['generation']
    assert delete(setup,0).status_code==409
    assert meta(setup)['generation']==generation
    action(setup,'copy',number=0);action(setup,'apply',number=1)
    generation=meta(setup)['generation']
    assert delete(setup,1).status_code==409
    assert meta(setup)['generation']==generation and meta(setup)['applied_revision']==1
    with setup.sessions() as db:
        assert db.query(ProductionPlanRevisionDeletion).count()==0
        assert db.query(ProductionPlanRevision).count()==2


def test_delete_requires_reason_permission_and_current_generation(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    setup.client.put(BASE,json=value(item,equipment));action(setup,'copy',number=0)
    stale=meta(setup)['generation'];action(setup,'copy',number=0)
    assert delete(setup,1,generation=stale).status_code==409
    assert delete(setup,1,reason='   ').status_code==422
    assert delete(setup,99).status_code==404
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{'/production/plans':'READ'}}))
    assert delete(setup,1).status_code==403
    with setup.sessions() as db:assert db.query(ProductionPlanRevisionDeletion).count()==0


def test_concurrent_apply_and_delete_cannot_remove_the_applied_revision(setup,monkeypatch):
    _,item,equipment,_=initialize(setup,monkeypatch)
    setup.client.put(BASE,json=value(item,equipment));action(setup,'copy',number=0)
    body={'month':'2026-10','generation':meta(setup)['generation'],'reason':'동시 요청','number':1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        removal=pool.submit(setup.client.request,'DELETE',BASE+'/revisions/1',json=body)
        application=pool.submit(setup.client.post,BASE+'/revisions/apply',json=body)
        results=[removal.result(),application.result()]
    assert sorted(r.status_code for r in results)==[200,409]
    with setup.sessions() as db:
        state=db.get(ProductionPlanRevisionState,'2026-10')
        assert db.query(ProductionPlanRevision).filter_by(month='2026-10',number=state.applied_revision).count()==1
