"""Authorization tests use real partner endpoints and isolated in-memory data."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base, get_db
from core.endpoint_access import menu_level, policies, resolve_policy, route_entries, route_table, enforce_endpoint_access
from core.security import get_current_user, check_admin_permission
from models.partner import Partner
from routers import partner, inventory, production_run, production_complete, basic_info, production_extra


@pytest.fixture
def guarded():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    user = SimpleNamespace(id=99, username='limited', role='USER', is_active=True, permissions=json.dumps({'menu_access':{}}))
    app = FastAPI()
    app.include_router(partner.router)
    app.include_router(basic_info.router)
    app.include_router(inventory.router)
    app.include_router(production_run.router)
    app.include_router(production_complete.router)
    app.include_router(production_extra.router)
    def db():
        with sessions() as session:
            yield session
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_current_user] = lambda: user
    # Execute the actual application middleware without importing app startup repairs.
    tree = ast.parse((Path(__file__).parents[1]/'app.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'enforce_menu_write_permission')
    class AuthDB:
        def __enter__(self):self.orm=sessions();return self
        def __exit__(self,*_):self.orm.close()
        def get(self,model,key):return self.orm.get(model,key)
        def query(self,model):
            return self if isinstance(model,SimpleNamespace) else self.orm.query(model)
        def filter(self,*_):return self
        def first(self):return user
    class Column:
        def __eq__(self,other):return True
    namespace = dict(app=app, Request=Request, JSONResponse=JSONResponse, DEV_BYPASS_AUTH=False,
        verify_session_token=lambda token: user.username if token=='valid' else None,
        SessionLocal=AuthDB, UserModel=SimpleNamespace(username=Column()), RedirectResponse=RedirectResponse)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),namespace)
    session_node=next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name=='enforce_login_session')
    exec(compile(ast.Module(body=[session_node],type_ignores=[]),'app.py','exec'),namespace)
    with TestClient(app) as client:
        client.cookies.set('session_token','valid')
        yield SimpleNamespace(client=client,app=app,user=user,sessions=sessions)
    engine.dispose()
    route_table.cache_clear()


def grant(ctx,access):ctx.user.permissions=json.dumps({'menu_access':access})


@pytest.mark.parametrize('headers',[{}, {'X-MES-Menu-Path':'/basic-info/partners'}, {'X-MES-Menu-Path':'/inventory/status'}, {'referer':'http://localhost/inventory/status'}])
def test_denied_partner_read_cannot_use_another_allowed_menu(guarded,headers):
    grant(guarded,{'/basic-info/partners':'NONE','/inventory/status':'WRITE'})
    assert guarded.client.get('/basic-info/partners/api/list',headers=headers).status_code==403
    assert guarded.client.get('/basic-info/partners/api/1',headers=headers).status_code==403


@pytest.mark.parametrize('method,path',[('POST','/basic-info/partners/api'),('PUT','/basic-info/partners/api/1'),('DELETE','/basic-info/partners/api/1')])
@pytest.mark.parametrize('headers',[{}, {'X-MES-Menu-Path':'/inventory/status'}, {'referer':'http://localhost/inventory/status'}])
def test_read_only_cannot_write_with_missing_or_forged_header(guarded,method,path,headers):
    grant(guarded,{'/basic-info/partners':'READ','/inventory/status':'WRITE'})
    assert guarded.client.request(method,path,headers=headers,json={'partner_code':'NO','partner_name':'No'}).status_code==403
    with guarded.sessions() as db:assert db.query(Partner).count()==0


def test_permitted_read_and_write_reach_real_handler(guarded):
    grant(guarded,{'/basic-info/partners':'WRITE'})
    response=guarded.client.post('/basic-info/partners/api',json={'partner_code':'V1','partner_name':'Allowed'})
    assert response.status_code==200,response.text
    grant(guarded,{'/basic-info/partners':'READ'})
    response=guarded.client.get('/basic-info/partners/api/list')
    assert response.status_code==200 and response.json()[0]['partner_code']=='V1'


@pytest.mark.parametrize('value',[None,'{}','invalid-json','[]',json.dumps({'menu_access':{}}),json.dumps({'menu_access':None})])
def test_missing_or_malformed_permissions_fail_closed(guarded,value):
    guarded.user.permissions=value
    assert guarded.client.get('/basic-info/partners/api/list').status_code==403


def test_no_auth_and_disabled_account(guarded):
    guarded.client.cookies.clear()
    assert guarded.client.get('/basic-info/partners/api/list').status_code==401
    guarded.client.cookies.set('session_token','valid')
    guarded.user.is_active=False
    assert guarded.client.get('/basic-info/partners/api/list').status_code==401


def test_admin_retains_access_but_admin_username_is_not_a_privilege(guarded):
    guarded.user.username='admin'
    assert not check_admin_permission(guarded.user)
    assert guarded.client.get('/basic-info/partners/api/list').status_code==403
    guarded.user.role='ADMIN'
    assert guarded.client.get('/basic-info/partners/api/list').status_code==200


def test_new_unregistered_endpoint_is_denied(guarded):
    @guarded.app.get('/api/new-private')
    def new_private():return {'secret':True}
    route_table.cache_clear()
    grant(guarded,{'/basic-info/partners':'WRITE'})
    assert guarded.client.get('/api/new-private').status_code==403


def test_trailing_slash_does_not_bypass_access(guarded):
    grant(guarded,{'/basic-info/partners':'NONE'})
    assert guarded.client.get('/basic-info/partners/api/list/').status_code==403


def test_read_only_post_exports_have_explicit_read_policy():
    rows=policies()
    key=('POST','/api/production/daily-job-report/excel','routers.daily_job_report:export')
    assert rows[key]['action']=='READ'


def test_legacy_account_compatibility_is_explicit_and_does_not_inherit_defaults():
    user=SimpleNamespace(permissions=json.dumps({'purchase':{'enabled':True}}))
    assert menu_level(user,'/purchase/inbound')=='READ'
    assert menu_level(user,'/inventory/lots')=='NONE'
    user.permissions='{}'
    assert menu_level(user,'/basic-info/items')=='NONE'
    user.permissions=json.dumps({'purchase':{'enabled':True},'menu_access':{}})
    assert menu_level(user,'/purchase/inbound')=='NONE'


def test_parameterized_route_matches_real_endpoint_identity(guarded):
    scope={'type':'http','path':'/basic-info/partners/api/123','method':'GET','root_path':'','app':guarded.app,'headers':[],'query_string':b''}
    request=Request(scope)
    policy,params=resolve_policy(request)
    assert policy['handler']=='routers.partner:get_partner_detail'
    assert params=={'partner_id':'123'}


def test_runtime_router_inventory_has_fixed_policies():
    # Importing router modules does not import app startup or touch production data.
    import importlib
    keys=set(policies())
    paths=Path(__file__).parents[1]/'core/endpoint_permissions.json'
    rows=json.loads(paths.read_text())
    modules={row['handler'].split(':')[0] for row in rows}
    found=set()
    for module in modules:
        imported=importlib.import_module(module)
        routers=[getattr(imported,name) for name in ('router','api_router','page_router') if hasattr(imported,name)]
        for router in routers:
            for path,methods,endpoint in route_entries(router.routes):
                for method in methods:
                    key=(method,path,endpoint.__module__+':'+endpoint.__name__)
                    assert key in keys, f'Missing permission registration: {key}'
                    found.add(key)
    assert len(found)>=470


def test_production_filtered_query_does_not_grant_other_type_or_unfiltered_list(guarded):
    grant(guarded,{'/production/performance/machining':'READ'})
    assert guarded.client.get('/api/production-run/runs?performance_type=MACHINING').status_code==200
    assert guarded.client.get('/api/production-run/runs?performance_type=ASSEMBLY').status_code==403
    assert guarded.client.get('/api/production-run/runs').status_code==403


def test_other_run_id_cannot_claim_a_different_type_or_use_forged_menu(guarded):
    from models.production_run import ProductionRun
    with guarded.sessions() as db:
        run=ProductionRun(work_order_id=1,performance_type='ASSEMBLY',performance_date='2026-11-09',process_code='C',operator_id=1,operator_name='worker',equipment_id=1,equipment_code='EQ',equipment_name='EQ',shift_type='DAY',start_time='2026-11-09 09:00')
        db.add(run);db.commit();identifier=run.id
    grant(guarded,{'/production/performance/machining':'WRITE'})
    headers={'X-MES-Menu-Path':'/production/performance/machining'}
    assert guarded.client.get(f'/api/production-run/{identifier}?performance_type=MACHINING',headers=headers).status_code==403
    assert guarded.client.post(f'/api/production-run/{identifier}/complete',json={},headers=headers).status_code==403


def test_bom_selection_has_minimal_lookup_without_full_master_access(guarded):
    from models.models import ItemMasterModel
    with guarded.sessions() as db:
        db.add(ItemMasterModel(part_no='BOM-1',part_name='BOM choice',account_type='반제품',material_type='동소재',unit='EA',created_at='now',note='internal note'))
        db.commit()
    grant(guarded,{'/basic-info/bom':'READ'})
    assert guarded.client.get('/api/basic-info/items').status_code==403
    response=guarded.client.get('/api/basic-info/item-options')
    assert response.status_code==200,response.text
    item=response.json()['data'][0]
    assert item['part_no']=='BOM-1'
    assert set(item)=={'id','part_no','part_name','unit','account_type','material_type'}


@pytest.mark.parametrize('url',['/basic-info/partners/api/list','/purchase/api/list','/api/inventory/status'])
def test_api_authentication_is_401_even_outside_api_prefix(guarded,url):
    guarded.client.cookies.clear()
    response=guarded.client.get(url,follow_redirects=False)
    assert response.status_code==401


def test_inventory_status_can_read_location_options_but_not_movement_history(guarded):
    from routers import inventory_lot_location
    guarded.app.include_router(inventory_lot_location.router)
    route_table.cache_clear()
    grant(guarded,{'/inventory/status':'READ'})
    assert guarded.client.get('/api/inventory/movements/locations').status_code==200
    assert guarded.client.get('/api/inventory/movements/history').status_code==403


def test_admin_named_account_without_admin_role_cannot_use_super_admin_helpers():
    from routers import admin_process_code,production_extra
    user=SimpleNamespace(username='admin',role='USER')
    assert not admin_process_code._is_admin(user)
    assert not admin_process_code._is_super_admin(user)
    assert not production_extra._is_super_admin(user)
    user.role='ADMIN'
    assert admin_process_code._is_admin(user)
    assert admin_process_code._is_super_admin(user)
    assert production_extra._is_super_admin(user)


def test_completed_performance_id_is_scoped_by_stored_type(guarded):
    from models.production import ProductionPerformance
    with guarded.sessions() as db:
        row=ProductionPerformance(work_order_id=1,performance_type='ASSEMBLY',performance_date='2026-11-09',process_code='C',good_qty=5)
        db.add(row);db.commit();identifier=row.id
    grant(guarded,{'/production/performance/machining':'WRITE'})
    response=guarded.client.delete(f'/api/production/performances/{identifier}?performance_type=MACHINING',headers={'X-MES-Menu-Path':'/production/performance/machining'})
    assert response.status_code==403
    with guarded.sessions() as db:assert db.get(ProductionPerformance,identifier) is not None


def test_start_request_body_is_preserved_after_authorization(guarded):
    grant(guarded,{'/production/performance/machining':'WRITE'})
    payload={'work_order_id':999,'performance_date':'2026-11-09','process_code':'A','operator_id':1,'equipment_id':1,'shift_type':'DAY','performance_type':'MACHINING'}
    response=guarded.client.post('/api/production-run/start',json=payload)
    assert response.status_code==400,response.text
    assert '작업지시' in response.json()['detail']


def test_malformed_type_does_not_crash_authorization(guarded):
    grant(guarded,{'/production/performance/machining':'WRITE','/production/performance/assembly':'WRITE'})
    response=guarded.client.post('/api/production-run/start',json={'performance_type':[]})
    assert response.status_code==422
