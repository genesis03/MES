import json
from types import SimpleNamespace
from routers import bom as bom_module
import pytest
from test_production_sync import setup,add_item,ADMIN
from core import security
from models.models import ItemMasterModel,ManualLabelModel
from routers.manual import router as manual_router
from routers.sales import router as sales_router
from routers.packing import router as packing_router
from routers.bom import router as bom_router
from routers.purchase import api_router as purchase_router, get_purchase_db
from routers.quality_reports import router as quality_router


@pytest.fixture
def data(setup,monkeypatch):
    monkeypatch.setattr(security,'get_current_user',lambda *args:ADMIN)
    monkeypatch.setattr(bom_module,'get_current_user_optional',lambda *args:SimpleNamespace(role='ADMIN',username='admin'))
    for router in [manual_router,sales_router,packing_router,bom_router,purchase_router,quality_router]:setup.app.include_router(router)
    def purchase_db():
        with setup.sessions() as db:yield db
    setup.app.dependency_overrides[get_purchase_db]=purchase_db
    with setup.sessions() as db:
        identifier=add_item(db,part='YR310313')
        row=db.get(ItemMasterModel,identifier);row.part_name='품목마스터 이름';row.material_type='FINISHED';row.account_type='PROD'
        db.commit()
    return setup,identifier


def test_manual_partial_search_and_save_canonical_part(data):
    setup,identifier=data
    result=setup.client.get('/api/manual/items',params={'q':'310313'}).json()
    assert result['total']==1 and result['items'][0]['part_no']=='YR310313'
    assert setup.client.get('/api/manual/item',params={'part_no':'310313'}).json()=={'part_name':'품목마스터 이름'}
    response=setup.client.post('/api/manual/add',json={'part_no':'310313','qty':'38','serial':'TEST','part_name':'라벨용 이름'})
    assert response.status_code==200,response.text
    with setup.sessions() as db:
        row=db.query(ManualLabelModel).one()
        assert row.item_id==identifier and row.part_no=='YR310313'
        assert row.part_name=='라벨용 이름' and row.barcode=='PYR310313Q38STEST'
        assert db.get(ItemMasterModel,identifier).part_name=='품목마스터 이름'


def test_ambiguous_search_lists_candidates_and_never_guesses(data):
    setup,identifier=data
    with setup.sessions() as db:add_item(db,part='YR310313-C');db.commit()
    results=setup.client.get('/api/manual/items',params={'q':'310313'}).json()
    assert results['total']==2
    assert {row['part_no'] for row in results['items']}=={'YR310313','YR310313-C'}
    assert setup.client.get('/api/manual/item',params={'part_no':'310313'}).json()['part_name'] is None
    response=setup.client.post('/api/manual/add',json={'part_no':'310313','qty':'38','serial':'TEST'})
    assert response.status_code==409
    with setup.sessions() as db:assert db.query(ManualLabelModel).count()==0
    response=setup.client.post('/api/manual/add',json={'part_no':'YR310313','qty':'38','serial':'TEST'})
    assert response.status_code==200


def test_exact_code_has_priority_and_lowercase_resolves_to_canonical(data):
    setup,identifier=data
    with setup.sessions() as db:add_item(db,part='310313');db.commit()
    response=setup.client.post('/api/manual/add',json={'part_no':'yr310313','qty':'1','serial':'T','barcode':'Pyr310313Q1ST'})
    assert response.status_code==200
    with setup.sessions() as db:
        row=db.query(ManualLabelModel).one();assert row.item_id==identifier and row.part_no=='YR310313' and row.barcode=='PYR310313Q1ST'


def test_search_treats_sql_wildcards_literally(data):
    setup,_=data
    assert setup.client.get('/api/manual/items',params={'q':'%'}).json()['total']==0
    assert setup.client.get('/api/manual/items',params={'q':'_'}).json()['total']==0


def test_quality_lookup_returns_canonical_part_and_history_filter_is_partial(data):
    setup,_=data
    response=setup.client.get('/api/quality/outbound-reports/standards',params={'part_no':'310313'})
    assert response.status_code==200,response.text
    assert response.json()['part_no']=='YR310313'
    assert response.json()['part_name']=='품목마스터 이름'


@pytest.mark.parametrize('path,params,root',[
    ('/api/sales/items',{'q':'310313'},None),
    ('/api/packing/items',{'q':'310313'},None),
    ('/api/bom/parent-items',{'keyword':'310313'},'data'),
    ('/api/purchase/items/search',{'keyword':'310313'},'items'),
])
def test_existing_registered_pickers_find_part_without_prefix(data,path,params,root):
    setup,_=data
    response=setup.client.get(path,params=params)
    assert response.status_code==200,response.text
    payload=response.json();rows=payload[root] if root else payload
    assert any(row['part_no']=='YR310313' for row in rows)
