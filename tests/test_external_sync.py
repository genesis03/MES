from types import SimpleNamespace
import pytest
from test_production_sync import setup, DAY
from core.security import get_current_user
from routers.external_sync import router, KINDS, _execute_group
from services import production_sync_service as sync

BASE='/api/admin/external-sync'


def test_common_settings_preserve_existing_values_until_explicit_save(setup):
    setup.app.include_router(router)
    with setup.sessions() as db:
        for index,kind in enumerate(KINDS):
            state=sync.state(db,kind); state.enabled=True;state.interval_minutes=60*(index+1);db.commit()
    data=setup.client.get(BASE+'/settings').json()
    assert data['interval_minutes'] is None
    assert [s['interval_minutes'] for s in data['states']]==[60,120,180]
    response=setup.client.put(BASE+'/settings',json={'enabled':True,'interval_minutes':240,'lookback_days':3,'targets':['production','subcontract']})
    assert response.status_code==200,response.text
    states=setup.client.get(BASE+'/settings').json()['states']
    assert [s['enabled'] for s in states]==[True,True,False]
    assert [s['interval_minutes'] for s in states]==[240,240,180]
    assert setup.client.put(BASE+'/settings',json={'enabled':True,'interval_minutes':240,'lookback_days':3,'targets':[]}).status_code==422
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='worker',role='USER',permissions=None)
    assert setup.client.get(BASE+'/settings').status_code==403


@pytest.mark.parametrize('tab,marker',[('production','esRows'),('subcontract','sfRows'),('packing','pkRows'),('history','exRuns')])
def test_tabs_share_one_settings_form_and_navigation(setup,tab,marker):
    page=setup.client.get('/admin/external-sync',params={'tab':tab})
    assert page.status_code==200,page.text
    assert marker in page.text
    assert page.text.count('id="exSettings"')==1
    assert page.text.count('id="esCredentials"')==1
    assert 'id="pkSettings"' not in page.text and 'id="sfSettings"' not in page.text and 'id="esSettings"' not in page.text
    assert '>외부 데이터 연동</a>' in page.text
    assert '>외부 연동 포장현황</a>' not in page.text


def test_batch_order_and_failure_does_not_stop_remaining_targets(setup,monkeypatch):
    setup.app.include_router(router)
    captured=[]
    import routers.external_sync as module
    class Thread:
        def __init__(self,**kw): captured.append(kw)
        def start(self): pass
    monkeypatch.setattr(module.threading,'Thread',Thread)
    response=setup.client.post(BASE+'/run',json={'start_date':DAY.isoformat(),'end_date':DAY.isoformat(),'targets':['packing','subcontract','production']})
    assert response.status_code==202,response.text
    assert [r['kind'] for r in response.json()['results']]==list(KINDS)
    calls=[]
    def execute(start,end,token,run_id,kind):
        calls.append(kind)
        if kind=='production':raise RuntimeError('test')
    monkeypatch.setattr(sync,'execute',execute)
    _execute_group(*captured[0]['args'])
    assert calls==list(KINDS)
    with setup.sessions() as db:
        State,Run,_,_=sync.backend('production')
        assert db.query(Run).one().status=='FAILED'
        assert db.get(State,1).lease_token is None
