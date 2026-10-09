"""Production sync uses temporary DBs and synthetic source responses only."""
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

_bootstrap = tempfile.TemporaryDirectory()
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + str(Path(_bootstrap.name) / 'bootstrap.db'))
from core.database import Base, get_db
from core.security import get_current_user
from models.models import ItemBomModel, ItemMasterModel, ProcessModel
from models.production_sync import ExternalProductionRecord, ProductionSyncRun, ProductionSyncState
from routers.production_sync import router
from routers.production_extra import router as performance_router
from models.production import ProductionPerformance, ProductionWorkOrder
from services import production_sync_client as source
from services import production_sync_service as sync
from services import production_sync_credentials as secrets
from routers import production_sync as api_module

DAY = date(2026, 3, 25)
ADMIN = SimpleNamespace(username='admin', role='SUPERADMIN', permissions=None)


def row(lot='TEST-LOT-001', day=DAY, **changes):
    result = {'ITEM_NUM': 'EXTERNAL-A', 'PRODUCT_NM': 'Example terminal', 'PROC_TYPE_NM': 'Test machining',
              'JOB_TIME': day.strftime('%Y%m%d'), 'JOB_ST_TIME': day.strftime('%Y%m%d') + '104408',
              'JOB_END_TIME': day.strftime('%Y%m%d') + '192500', 'JOB_NUM': 'TEST-JOB-001', 'LOT_NUM': lot,
              'JOB_QTY': '216', 'LOT_QTY': '331', 'FAULT_QTY': '0', 'F10': '115'}
    return result | changes


def response(rows):
    return {'Common.Set.LotSet.' + key: [','.join(r[key] for r in rows)] for key in row()} | {'SO.NET.END': ['SO.NET.END']}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'test.db'), connect_args={'check_same_thread': False, 'timeout': 30})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(sync, 'SessionLocal', sessions)
    monkeypatch.setattr(secrets, 'SessionLocal', sessions)
    monkeypatch.setattr(secrets.config, 'PRODUCTION_SYNC_KEY_PATH', tmp_path / 'sync.key')
    monkeypatch.setattr(sync.config, 'PRODUCTION_SYNC_USER', 'test-user')
    monkeypatch.setattr(sync.config, 'PRODUCTION_SYNC_PASSWORD', 'test-password')
    monkeypatch.setattr(sync, 'today', lambda: DAY)
    app = FastAPI(); app.include_router(router); app.include_router(performance_router)
    def db_override():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    with TestClient(app) as client:
        yield SimpleNamespace(engine=engine, sessions=sessions, app=app, client=client)
    engine.dispose()


def lease(setup, trigger='MANUAL'):
    return sync.claim(DAY, DAY, trigger)


def test_adapter_uses_exact_command_dates_and_preserves_setup(monkeypatch):
    client = source.ProductionClient.__new__(source.ProductionClient)
    client.opener = object(); client.context = {'Common.Set.LoginUserSet.USER_ID': 'test-user'}
    captured = []
    def post(opener, payload):
        captured.append(payload)
        return response([row()])
    monkeypatch.setattr(source, 'post', post)
    rows, warnings = client.fetch_day(DAY)
    assert rows == [row()] and not warnings
    assert captured[0]['run.object.name'] == 'MES.Product.ProcLotList.ProcLotListObj'
    assert captured[0]['jump.form.code'] == 'List'
    assert captured[0]['MES.Product.Set.JobCondSet.JOB_DATE1'] == (DAY - timedelta(days=1)).isoformat()
    assert captured[0]['MES.Product.Set.JobCondSet.JOB_DATE2'] == (DAY + timedelta(days=1)).isoformat()
    assert captured[0]['MES.Product.Set.JobCondSet.PROC_TYPE'] == ''


def test_padded_query_recovers_rows_from_exclusive_bounds_and_filters_neighbors(monkeypatch):
    client = source.ProductionClient.__new__(source.ProductionClient); client.opener = object(); client.context = {}
    def reply(_, payload):
        start = date.fromisoformat(payload['MES.Product.Set.JobCondSet.JOB_DATE1'])
        end = date.fromisoformat(payload['MES.Product.Set.JobCondSet.JOB_DATE2'])
        if start == end: return empty_query_response()
        return response([row('PREVIOUS', DAY - timedelta(days=1)), row(),
                         row('NEXT', DAY + timedelta(days=1))])
    monkeypatch.setattr(source, 'post', reply)
    rows, _ = client.fetch_day(DAY)
    assert rows == [row()]


def test_zero_year_import_does_not_complete_initial_sync(setup, monkeypatch):
    class Client:
        def __init__(self, *_): pass
        def fetch_day(self, day): return [], []
    monkeypatch.setattr(sync, 'ProductionClient', Client)
    with setup.sessions() as db:
        settings = sync.state(db); settings.enabled = True; db.commit()
    start = DAY.replace(month=1, day=1)
    token, run_id = sync.claim(start, DAY, 'INITIAL')
    sync.execute(start, DAY, token, run_id)
    with setup.sessions() as db:
        run = db.get(ProductionSyncRun, run_id)
        assert run.status == 'FAILED' and '0건' in run.error
        assert db.get(ProductionSyncState, 1).initial_completed_at is None


@pytest.mark.parametrize('change', [{'F10': ''}, {'F10': '-1'}, {'JOB_QTY': 'NaN'}, {'JOB_TIME': '20260322'}, {'JOB_ST_TIME': 'invalid'}, {'LOT_NUM': ''}])
def test_adapter_rejects_invalid_rows(monkeypatch, change):
    client = source.ProductionClient.__new__(source.ProductionClient); client.opener = object(); client.context = {}
    monkeypatch.setattr(source, 'post', lambda *_: response([row(**change)]))
    with pytest.raises(source.SyncError):
        client.fetch_day(DAY)


@pytest.mark.parametrize('change, field', [
    ({'F10': ''}, 'F10'), ({'JOB_TIME': 'invalid'}, 'JOB_TIME'),
    ({'JOB_END_TIME': '20260325250000'}, 'JOB_END_TIME')])
def test_format_error_identifies_row_and_field_without_retry(monkeypatch, change, field):
    client = source.ProductionClient.__new__(source.ProductionClient); client.opener = object(); client.context = {}
    calls = []
    def reply(*_):
        calls.append(1)
        return response([row(**change)])
    monkeypatch.setattr(source, 'post', reply)
    with pytest.raises(source.SyncError) as error:
        client.fetch_day(DAY)
    assert '1행' in str(error.value) and field in str(error.value)
    assert len(calls) == 1


def test_transient_timeout_retries_same_day_without_duplicate_save(setup, monkeypatch):
    client = source.ProductionClient.__new__(source.ProductionClient); client.opener = object(); client.context = {}
    calls = []
    def reply(_, payload):
        calls.append(payload['MES.Product.Set.JobCondSet.JOB_DATE1'])
        if len(calls) < 3: raise TimeoutError('private transport details')
        return response([row()])
    monkeypatch.setattr(source, 'post', reply)
    monkeypatch.setattr(source.time, 'sleep', lambda _: None)
    monkeypatch.setattr(sync, 'ProductionClient', lambda *_: client)
    token, run_id = lease(setup)
    sync.execute(DAY, DAY, token, run_id)
    assert calls == [(DAY - timedelta(days=1)).isoformat()] * 3
    with setup.sessions() as db:
        assert db.get(ProductionSyncRun, run_id).status == 'SUCCESS'
        assert db.query(ExternalProductionRecord).count() == 1


def test_exhausted_transport_retry_is_distinct_from_format_error(monkeypatch):
    monkeypatch.setattr(source.time, 'sleep', lambda _: None)
    calls = []
    def fail(*_):
        calls.append(1); raise TimeoutError('private transport details')
    monkeypatch.setattr(source, 'post', fail)
    with pytest.raises(source.SyncError) as error:
        source.query_with_retry(object(), {})
    assert len(calls) == 3 and '총 3회' in str(error.value) and '응답 시간' in str(error.value)
    assert 'private transport details' not in str(error.value)


def test_http_auth_failure_is_not_retried(monkeypatch):
    calls = []
    def fail(*_):
        calls.append(1); raise source.HTTPError('url', 401, 'private', {}, None)
    monkeypatch.setattr(source, 'post', fail)
    with pytest.raises(source.SyncError, match='HTTP 오류\\(401\\)'):
        source.query_with_retry(object(), {})
    assert len(calls) == 1


def test_ambiguous_product_name_does_not_shift_quantities(monkeypatch):
    client = source.ProductionClient.__new__(source.ProductionClient); client.opener = object(); client.context = {}
    monkeypatch.setattr(source, 'post', lambda *_: response([row(PRODUCT_NM='A,B')]))
    rows, warnings = client.fetch_day(DAY)
    assert rows[0]['PRODUCT_NM'] == '' and rows[0]['F10'] == '115' and warnings


def empty_query_response():
    return {'SO.NET.END': ['SO.NET.END'],
            'log.invoke.object': ['MES.Product.ProcLotL_20261008151137273_902791'],
            'MONITOR.QUERY.START': ['2026-10-08 15:11:37'],
            'MONITOR.QUERY.END': ['2026-10-08 15:11:38']}


def test_empty_day_without_dataset_continues_to_next_day(setup, monkeypatch):
    client = source.ProductionClient.__new__(source.ProductionClient)
    client.opener = object(); client.context = {}
    requested = []
    def post(_, payload):
        requested.append(payload['MES.Product.Set.JobCondSet.JOB_DATE1'])
        return empty_query_response() if len(requested) == 1 else response([row()])
    monkeypatch.setattr(source, 'post', post)
    monkeypatch.setattr(sync, 'ProductionClient', lambda *_: client)
    with setup.sessions() as db:
        settings = sync.state(db); settings.enabled = True; db.commit()
    start = DAY - timedelta(days=1)
    token, run_id = sync.claim(start, DAY, 'INITIAL')
    sync.execute(start, DAY, token, run_id)
    assert requested == [(start - timedelta(days=1)).isoformat(), (DAY - timedelta(days=1)).isoformat()]
    with setup.sessions() as db:
        run = db.get(ProductionSyncRun, run_id)
        assert run.status == 'SUCCESS'
        counts = json.loads(run.counts_json)
        assert counts['completed_days'] == 2 and counts['inserted'] == 1
        assert db.get(ProductionSyncState, 1).initial_completed_at is not None


@pytest.mark.parametrize('change', [
    {'log.invoke.object': ['Common.Login.LoginOb_123']},
    {'MONITOR.QUERY.END': ['']},
    {'jump.form.code': ['Login']},
    {'jump.url': ['/Login.aspx']},
    {'jump.form.message': ['Login required']},
    {'Common.Set.LotSet.ROW_COUNT': ['1']},
    {'Common.Set.LotSet.JOB_QTY': ['216']},
])
def test_missing_dataset_is_not_treated_as_empty_when_response_is_uncertain(monkeypatch, change):
    client = source.ProductionClient.__new__(source.ProductionClient)
    client.opener = object(); client.context = {}
    monkeypatch.setattr(source, 'post', lambda *_: empty_query_response() | change)
    with pytest.raises(source.SyncError):
        client.fetch_day(DAY)


@pytest.mark.parametrize('change', [{'Common.Set.LotSet.ROW_COUNT': ['2']}, {'Common.Set.LotSet.F10': ['115,3']}])
def test_adapter_rejects_count_mismatch(monkeypatch, change):
    client = source.ProductionClient.__new__(source.ProductionClient); client.opener = object(); client.context = {}
    monkeypatch.setattr(source, 'post', lambda *_: response([row()]) | change)
    with pytest.raises(source.SyncError):
        client.fetch_day(DAY)


def test_post_places_end_marker_last_and_does_not_leak_server_exception():
    class Reply:
        headers = SimpleNamespace(get_content_charset=lambda: 'utf-8')
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, *_): return urlencode({'SO.NET.END': 'SO.NET.END', 'THROW.MESS': 'private-password'}).encode()
    class Opener:
        def open(self, request, **kwargs):
            assert request.data.decode().endswith('SO.NET.END=SO.NET.END')
            assert parse_qs(request.data.decode())['run.object.name'] == ['Example.List']
            return Reply()
    with pytest.raises(source.SyncError) as error:
        source.post(Opener(), {'SO.NET.END': 'SO.NET.END', 'run.object.name': 'Example.List'})
    assert 'private-password' not in str(error.value)


def test_save_is_idempotent_updates_and_preserves_unregistered_parts(setup):
    token, _ = lease(setup)
    assert sync.save_day([row()], token)['inserted'] == 1
    assert sync.save_day([row()], token)['unchanged'] == 1
    assert sync.save_day([row(JOB_QTY='217', LOT_QTY='332')], token)['updated'] == 1
    with setup.sessions() as db:
        assert db.query(ExternalProductionRecord).count() == 1
        record = db.query(ExternalProductionRecord).one()
        assert record.part_no == 'EXTERNAL-A' and record.job_qty == '217'
        assert json.loads(record.raw_json)['F10'] == '115'
        assert db.query(ItemMasterModel).count() == 0


def test_duplicate_rows_and_expired_lease_do_not_write(setup):
    token, _ = lease(setup)
    with pytest.raises(source.SyncError): sync.save_day([row(), row()], token)
    with setup.sessions() as db:
        state = db.get(ProductionSyncState, 1); state.lease_until = sync.now() - timedelta(seconds=1); db.commit()
    with pytest.raises(source.SyncError): sync.save_day([row()], token)
    with setup.sessions() as db: assert db.query(ExternalProductionRecord).count() == 0


def test_concurrent_claims_have_single_owner(setup):
    with setup.sessions() as db: sync.state(db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: lease(setup), range(4)))
    assert sum(claim is not None for claim in claims) == 1
    with setup.sessions() as db: assert db.query(ProductionSyncRun).filter_by(status='RUNNING').count() == 1


def test_partial_failure_records_progress_and_does_not_complete_initial(setup, monkeypatch):
    start = DAY - timedelta(days=1)
    with setup.sessions() as db:
        settings = sync.state(db); settings.enabled = True; db.commit()
    class Client:
        def __init__(self, *_): pass
        def fetch_day(self, day):
            if day == DAY: raise source.SyncError('Test day failed')
            return [row(day=day)], []
    monkeypatch.setattr(sync, 'ProductionClient', Client)
    token, run_id = sync.claim(start, DAY, 'INITIAL')
    sync.execute(start, DAY, token, run_id)
    with setup.sessions() as db:
        run = db.get(ProductionSyncRun, run_id)
        assert run.status == 'FAILED' and json.loads(run.counts_json)['completed_days'] == 1
        assert run.error.startswith(DAY.isoformat() + ' 조회:')
        assert db.get(ProductionSyncState, 1).initial_completed_at is None
        assert db.query(ExternalProductionRecord).count() == 1
        assert db.get(ProductionSyncState, 1).lease_token is None


def test_scheduler_fetches_january_once_then_recent_period(setup, monkeypatch):
    class OneCycle:
        def __init__(self): self.calls = 0
        def wait(self, *_): self.calls += 1; return self.calls > 1
    class Client:
        def __init__(self, *_): pass
        def fetch_day(self, day): return ([row()], []) if day == DAY else ([], [])
    monkeypatch.setattr(sync, 'ProductionClient', Client)
    with setup.sessions() as db:
        settings = sync.state(db); settings.enabled = True; db.commit()
    monkeypatch.setattr(sync, '_stop', OneCycle()); sync.loop()
    with setup.sessions() as db:
        run = db.query(ProductionSyncRun).one()
        assert run.start_date == '2026-01-01' and run.end_date == DAY.isoformat()
        assert run.trigger == 'INITIAL' and run.status == 'SUCCESS'
        settings = db.get(ProductionSyncState, 1)
        assert settings.initial_completed_at is not None
        settings.next_run_at = sync.now() - timedelta(seconds=1); db.commit()
    monkeypatch.setattr(sync, '_stop', OneCycle()); sync.loop()
    with setup.sessions() as db:
        run = db.query(ProductionSyncRun).order_by(ProductionSyncRun.id.desc()).first()
        assert run.trigger == 'AUTO' and run.start_date == (DAY - timedelta(days=6)).isoformat()


def add_item(db, part='LOCAL-B', code='LT'):
    item = ItemMasterModel(part_no=part, part_name='Registered local product', account_type='반제품', material_type='기타', production_loc=code, is_active='Y', created_at='2026-01-01', updated_at='2026-01-01')
    db.add(item); db.commit(); return item.id


def test_api_late_registration_mapping_search_and_setup(setup):
    token, _ = lease(setup); sync.save_day([row()], token)
    result = setup.client.get('/api/production/external-sync/records').json()['rows'][0]
    assert result['mes_part_no'] == '' and result['setup_qty'] == '115' and result['good_qty'] == '216'
    assert '품번 연결 확인' in result['notes']
    with setup.sessions() as db:
        db.add(ProcessModel(process_code='LT', process_name='Internal lathe', created_at='2026-01-01'))
        item_id = add_item(db)
    assert setup.client.put('/api/production/external-sync/process-maps', json={'source_name':'Test machining','process_code':'LT','performance_type':'MACHINING'}).status_code == 200
    candidates = setup.client.get('/api/production/external-sync/item-candidates', params={'source_process':'Test machining','keyword':'LOCAL'}).json()
    assert candidates['total'] == 1
    assert setup.client.put('/api/production/external-sync/item-maps', json={'source_part_no':'EXTERNAL-A','source_process':'Test machining','item_id':item_id}).status_code == 200
    result = setup.client.get('/api/production/external-sync/records', params={'keyword':'LOCAL-B'}).json()
    assert result['total'] == 1
    record = result['rows'][0]
    assert record['part_no'] == 'EXTERNAL-A' and record['mes_part_no'] == 'LOCAL-B'
    assert record['performance_type'] == 'MACHINING' and record['notes'] == []
    assert setup.client.get('/api/production/external-sync/records', params={'keyword':'LOCAL_B'}).json()['total'] == 0


def test_api_allows_manual_selection_with_different_process_and_flags_quantity_difference(setup):
    token, _ = lease(setup); sync.save_day([row(LOT_QTY='999')], token)
    with setup.sessions() as db:
        db.add(ProcessModel(process_code='LT', process_name='Test machining', created_at='2026-01-01'))
        item_id = add_item(db, code='TP')
    reply = setup.client.put('/api/production/external-sync/item-maps', json={'source_part_no':'EXTERNAL-A','source_process':'Test machining','item_id':item_id})
    assert reply.status_code == 200
    assert '전체수량과 양품·불량·셋업 합계 확인' in setup.client.get('/api/production/external-sync/records').json()['rows'][0]['notes']


@pytest.mark.parametrize('process, source_part, target, account', [
    ('복합선반', '310061', '310061-A', '반제품'),
    ('탭핑가공', '310061-A', '310061-B', '반제품'),
    ('세레이션', '310061-B', '310061-D', '반제품'),
    ('은도금 외주 가공 후 입고', '310061-D', '310061-Ag', '반제품'),
    ('캡조립', '310061', '310061-C', '반제품'),
    ('조립', '310061-Ag', '310061-c', '반제품'),
    ('포장', '310061-C', '310061', '완제품'),
])
def test_stage_auto_connection_is_consistent_and_searchable(setup, process, source_part, target, account):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM=source_part, PROC_TYPE_NM=process)], token)
    with setup.sessions() as db:
        item_id = add_item(db, part=target)
        db.get(ItemMasterModel, item_id).account_type = account; db.commit()
    listed = setup.client.get('/api/production/external-sync/item-maps').json()[0]
    assert listed['part_no'] == target and listed['connection_type'] == 'AUTO_STAGE'
    assert listed['linked'] and not listed['explicit']
    records = setup.client.get('/api/production/external-sync/records', params={'keyword':target}).json()
    assert records['total'] == 1
    assert records['rows'][0]['mes_part_no'] == target and records['rows'][0]['part_no'] == source_part


def test_assembly_does_not_fall_back_to_finished_item_then_links_after_registration(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db: add_item(db, part='310061')
    assert setup.client.get('/api/production/external-sync/item-maps').json()[0]['part_no'] == ''
    with setup.sessions() as db: add_item(db, part='310061-C')
    assert setup.client.get('/api/production/external-sync/records').json()['rows'][0]['mes_part_no'] == '310061-C'


def test_manual_override_with_different_base_and_reset_to_auto(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db:
        add_item(db, part='310061-C')
        manual_id = add_item(db, part='LOCAL-C', code='OTHER')
    candidates = setup.client.get('/api/production/external-sync/item-candidates', params={'source_process':'조립', 'keyword':'LOCAL-C'}).json()
    assert [r['id'] for r in candidates['rows']] == [manual_id]
    payload = {'source_part_no':'310061', 'source_process':'조립', 'item_id':manual_id}
    assert setup.client.put('/api/production/external-sync/item-maps', json=payload).status_code == 200
    linked = setup.client.get('/api/production/external-sync/item-maps').json()[0]
    assert linked['part_no'] == 'LOCAL-C' and linked['connection_type'] == 'MANUAL' and linked['explicit']
    assert setup.client.get('/api/production/external-sync/records', params={'keyword':'LOCAL-C'}).json()['total'] == 1
    assert setup.client.put('/api/production/external-sync/item-maps', json=payload | {'item_id':None}).status_code == 200
    assert setup.client.get('/api/production/external-sync/item-maps').json()[0]['part_no'] == '310061-C'


def test_inactive_or_case_ambiguous_stage_candidates_are_not_auto_linked(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db:
        upper_id = add_item(db, part='310061-C')
        lower_id = add_item(db, part='310061-c')
    assert not setup.client.get('/api/production/external-sync/item-maps').json()[0]['linked']
    with setup.sessions() as db:
        db.get(ItemMasterModel, lower_id).is_active = 'N'; db.commit()
    assert setup.client.get('/api/production/external-sync/item-maps').json()[0]['item_id'] == upper_id
    assert setup.client.put('/api/production/external-sync/item-maps', json={'source_part_no':'310061', 'source_process':'조립', 'item_id':lower_id}).status_code == 422


def test_mapped_internal_process_controls_stage_and_candidate_order(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='Legacy process')], token)
    with setup.sessions() as db:
        db.add(ProcessModel(process_code='AS', process_name='조립', created_at='2026-01-01'))
        c_id = add_item(db, part='310061-C')
        add_item(db, part='000001-A')
    assert setup.client.put('/api/production/external-sync/process-maps', json={'source_name':'Legacy process', 'process_code':'AS', 'performance_type':'ASSEMBLY'}).status_code == 200
    record = setup.client.get('/api/production/external-sync/records').json()['rows'][0]
    assert record['mes_part_no'] == '310061-C' and record['performance_type'] == 'ASSEMBLY'
    candidates = setup.client.get('/api/production/external-sync/item-candidates', params={'source_process':'Legacy process'}).json()
    assert candidates['rows'][0]['id'] == c_id


def test_numeric_source_variant_is_not_guessed_and_can_be_manually_connected(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061-1', PROC_TYPE_NM='캡조립')], token)
    with setup.sessions() as db: item_id = add_item(db, part='310061-C')
    assert not setup.client.get('/api/production/external-sync/item-maps').json()[0]['linked']
    assert setup.client.put('/api/production/external-sync/item-maps', json={'source_part_no':'310061-1', 'source_process':'캡조립', 'item_id':item_id}).status_code == 200
    assert setup.client.get('/api/production/external-sync/records').json()['rows'][0]['mes_part_no'] == '310061-C'


def test_packing_requires_finished_item_and_ambiguous_process_requires_manual_choice(setup):
    token, _ = lease(setup)
    sync.save_day([row('PACK', ITEM_NUM='310061-C', PROC_TYPE_NM='포장'),
                   row('COMPOUND', ITEM_NUM='310061', PROC_TYPE_NM='조립/포장')], token)
    with setup.sessions() as db: add_item(db, part='310061')
    assert all(not r['linked'] for r in setup.client.get('/api/production/external-sync/item-maps').json())


def add_bom(db, parent, child, part_only=False):
    db.add(ItemBomModel(parent_item_id=None if part_only else parent.id,
                        child_item_id=None if part_only else child.id,
                        parent_part_no=parent.part_no, child_part_no=child.part_no,
                        created_at='2026-01-01'))
    db.commit()


@pytest.mark.parametrize('process, target', [
    ('복합선반', '310062-A'), ('탭핑', '310062-B'), ('세레이션', '310062-D'),
    ('은도금 외주 가공 후 입고', '310062-Ag'), ('캡조립', '310062-c'),
])
def test_bom_connects_different_base_parts_at_each_production_stage(setup, process, target):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM=process)], token)
    with setup.sessions() as db:
        chain = []
        for part in ['310061', '310062-c', '310062-Ag', '310062-D', '310062-B', '310062-A']:
            chain.append(db.get(ItemMasterModel, add_item(db, part=part)))
        chain[0].account_type = '완제품'; db.commit()
        for parent, child in zip(chain, chain[1:]): add_bom(db, parent, child)
    listed = setup.client.get('/api/production/external-sync/item-maps').json()[0]
    assert listed['part_no'] == target and listed['connection_type'] == 'AUTO_BOM'
    record = setup.client.get('/api/production/external-sync/records', params={'keyword':target}).json()
    assert record['total'] == 1 and record['rows'][0]['mes_part_no'] == target
    candidates = setup.client.get('/api/production/external-sync/item-candidates', params={'source_process':process,'source_part_no':'310061'}).json()
    assert candidates['rows'][0]['part_no'] == target


def test_bom_ambiguity_does_not_fall_back_to_same_base_suffix_and_manual_wins(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db:
        parent = db.get(ItemMasterModel, add_item(db, part='310061'))
        first = db.get(ItemMasterModel, add_item(db, part='310062-C'))
        second = db.get(ItemMasterModel, add_item(db, part='310063-C'))
        add_item(db, part='310061-C')
        add_bom(db, parent, first); add_bom(db, parent, second)
        manual_id = second.id
    assert not setup.client.get('/api/production/external-sync/item-maps').json()[0]['linked']
    assert setup.client.put('/api/production/external-sync/item-maps', json={'source_part_no':'310061','source_process':'조립','item_id':manual_id}).status_code == 200
    assert setup.client.get('/api/production/external-sync/records').json()['rows'][0]['mes_part_no'] == '310063-C'


def test_bom_part_only_links_and_cycles_are_handled(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db:
        parent = db.get(ItemMasterModel, add_item(db, part='310061'))
        child = db.get(ItemMasterModel, add_item(db, part='310062-C'))
        add_bom(db, parent, child, part_only=True)
        add_bom(db, child, parent, part_only=True)
    assert setup.client.get('/api/production/external-sync/item-maps').json()[0]['part_no'] == '310062-C'


def test_bom_packing_finds_different_finished_parent(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310062-C', PROC_TYPE_NM='포장')], token)
    with setup.sessions() as db:
        parent = db.get(ItemMasterModel, add_item(db, part='310061'))
        parent.account_type = '완제품'; db.commit()
        child = db.get(ItemMasterModel, add_item(db, part='310062-C'))
        add_bom(db, parent, child)
    record = setup.client.get('/api/production/external-sync/records').json()['rows'][0]
    assert record['mes_part_no'] == '310061' and record['connection_type'] == 'AUTO_BOM'


@pytest.mark.parametrize('source_part', ['310062-C', '310062-Ag'])
def test_bom_connections_from_registered_intermediate_part(setup, source_part):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM=source_part, PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db:
        parent = db.get(ItemMasterModel, add_item(db, part='310061'))
        assembly = db.get(ItemMasterModel, add_item(db, part='310062-C'))
        silver = db.get(ItemMasterModel, add_item(db, part='310062-Ag'))
        add_bom(db, parent, assembly); add_bom(db, assembly, silver)
    assert setup.client.get('/api/production/external-sync/records').json()['rows'][0]['mes_part_no'] == '310062-C'


def test_bom_prefers_real_child_over_same_base_guess_and_respects_inactive_items(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310061', PROC_TYPE_NM='조립')], token)
    with setup.sessions() as db:
        parent = db.get(ItemMasterModel, add_item(db, part='310061'))
        add_item(db, part='310061-C')
        child = db.get(ItemMasterModel, add_item(db, part='310062-C'))
        child_id = child.id
        add_bom(db, parent, child)
    assert setup.client.get('/api/production/external-sync/item-maps').json()[0]['part_no'] == '310062-C'
    with setup.sessions() as db:
        db.get(ItemMasterModel, child_id).is_active = 'N'; db.commit()
    assert not setup.client.get('/api/production/external-sync/item-maps').json()[0]['linked']


def test_readonly_user_cannot_change_settings_or_start_run(setup):
    setup.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username='reader', role='USER', permissions=json.dumps({'menu_access':{'/production/performance/status':'READ'}}))
    assert setup.client.get('/api/production/external-sync/settings').status_code == 403
    assert setup.client.get('/admin/production-sync').status_code == 403
    assert setup.client.put('/api/production/external-sync/settings',json={'enabled':True}).status_code == 403
    assert setup.client.post('/api/production/external-sync/run',json={'start_date':DAY.isoformat(),'end_date':DAY.isoformat()}).status_code == 403


def test_missing_credentials_disable_enable_and_manual_requests(setup, monkeypatch):
    monkeypatch.setattr(sync.config,'PRODUCTION_SYNC_PASSWORD','')
    assert setup.client.get('/api/production/external-sync/settings').json()['credentials_ready'] is False
    assert setup.client.put('/api/production/external-sync/settings',json={'enabled':True}).status_code == 422
    assert setup.client.post('/api/production/external-sync/run',json={'start_date':DAY.isoformat(),'end_date':DAY.isoformat()}).status_code == 409


def test_pagination_and_empty_day_preserve_previous_records(setup):
    token, _ = lease(setup)
    sync.save_day([row(lot=f'LOT-{i}') for i in range(3)], token)
    sync.save_day([], token)
    first = setup.client.get('/api/production/external-sync/records',params={'page_size':2}).json()
    second = setup.client.get('/api/production/external-sync/records',params={'page_size':2,'page':2}).json()
    assert first['total'] == 3 and len(first['rows']) == 2 and len(second['rows']) == 1
    assert set(r['id'] for r in first['rows']).isdisjoint(r['id'] for r in second['rows'])
    assert setup.client.get('/production/performance/external').status_code == 200


def test_http_login_cookie_and_list_protocol(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'login page')
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length'])).decode()
            assert raw.endswith('SO.NET.END=SO.NET.END')
            payload = parse_qs(raw, keep_blank_values=True)
            action = payload['jump.form.code'][0]; seen.append(action)
            if action == 'Login':
                assert payload['Common.Set.LoginShortSet.USER_ID'] == ['test-user']
                assert payload['run.object.name'] == ['Common.Login.LoginObj']
                body = {'Common.Set.LoginUserSet.USER_ID':'test-user', 'Common.Set.LoginUserSet.COMP_NO':'company-id',
                        'Common.Set.LoginUserSet.USER_PASS':'', 'SO.NET.END':'SO.NET.END'}
                self.send_response(200); self.send_header('Set-Cookie','ASP.NET_SessionId=synthetic-session; Path=/')
            else:
                assert 'ASP.NET_SessionId=synthetic-session' in self.headers.get('Cookie', '')
                assert payload['Common.Set.LoginUserSet.COMP_NO'] == ['company-id']
                assert payload['run.object.name'] == ['MES.Product.ProcLotList.ProcLotListObj']
                body = {key: values[0] for key, values in response([row()]).items()}
                self.send_response(200)
            self.send_header('Content-Type','application/x-www-form-urlencoded; charset=utf-8'); self.end_headers()
            self.wfile.write(urlencode(body).encode())
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(source,'BASE',base); monkeypatch.setattr(source,'ENDPOINT',base+'/HttpPort.aspx')
    try:
        client = source.ProductionClient('test-user','test-password')
        rows, warnings = client.fetch_day(DAY)
        assert rows == [row()] and not warnings and seen == ['Login','List']
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_late_worker_cannot_release_new_lease(setup, monkeypatch):
    token, old_run = lease(setup)
    with setup.sessions() as db:
        settings = db.get(ProductionSyncState,1); settings.lease_until = sync.now()-timedelta(seconds=1); db.commit()
    new_token, new_run = lease(setup)
    class Client:
        def __init__(self,*_): pass
        def fetch_day(self,day): return [row()],[]
    monkeypatch.setattr(sync,'ProductionClient',Client)
    sync.execute(DAY,DAY,token,old_run)
    with setup.sessions() as db:
        assert db.get(ProductionSyncState,1).lease_token == new_token
        assert db.get(ProductionSyncRun,new_run).status == 'RUNNING'
        assert db.get(ProductionSyncRun,old_run).status == 'FAILED'
        assert db.query(ExternalProductionRecord).count() == 0


def test_external_records_do_not_create_native_performance_or_lots(setup):
    from models.production import ProductionPerformance, ProductionWorkOrder
    from models.production_run import ProductionRun
    from models.production_lot import ProductionLotModel
    from models.lot_consumption import LotConsumptionModel
    token,_ = lease(setup); sync.save_day([row()],token)
    with setup.sessions() as db:
        assert db.query(ExternalProductionRecord).count() == 1
        for model in [ProductionPerformance,ProductionWorkOrder,ProductionRun,ProductionLotModel,LotConsumptionModel]:
            assert db.query(model).count() == 0



def test_credentials_are_encrypted_and_applied_without_restart(setup, monkeypatch):
    seen=[]
    monkeypatch.setattr(api_module,'ProductionClient',lambda user,password:seen.append((user,password)))
    password='synthetic-new-password'
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'new-user','password':password}).status_code==200
    from models.production_sync import ProductionSyncCredential
    with setup.sessions() as db:
        stored=db.get(ProductionSyncCredential,1)
        assert stored.encrypted_password != password and password not in stored.encrypted_password
        assert secrets.read_credentials(db)==('new-user',password)
    result=setup.client.get('/api/production/external-sync/credentials')
    assert result.json()['configured'] is True and 'password' not in result.json() and password not in result.text
    assert secrets.read_credentials()==('new-user',password)
    assert sync.credentials_ready() is True
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'new-user','password':''}).status_code==200
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'another-user','password':''}).status_code==422


def test_bad_login_does_not_replace_credentials(setup,monkeypatch):
    with setup.sessions() as db:secrets.save_credentials(db,'kept-user','kept-password')
    def fail(*_):raise source.SyncError('로그인 실패')
    monkeypatch.setattr(api_module,'ProductionClient',fail)
    result=setup.client.put('/api/production/external-sync/credentials',json={'username':'wrong','password':'wrong-password'})
    assert result.status_code==422
    assert secrets.read_credentials()==('kept-user','kept-password')


def test_wrong_server_key_requires_reconfiguration(setup,monkeypatch):
    with setup.sessions() as db:secrets.save_credentials(db,'kept-user','kept-password')
    from pathlib import Path
    monkeypatch.setattr(secrets.config,'PRODUCTION_SYNC_KEY_PATH',Path(setup.engine.url.database+'.different.key'))
    result=setup.client.get('/api/production/external-sync/credentials').json()
    assert not result['configured'] and result['error']
    monkeypatch.setattr(api_module,'ProductionClient',lambda *_:None)
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'kept-user','password':'reset-password'}).status_code==200
    assert secrets.read_credentials()==('kept-user','reset-password')


def test_admin_menu_move_and_old_url_redirect(setup):
    page=setup.client.get('/admin/production-sync')
    assert page.status_code==200 and 'esCredentials' in page.text
    assert 'nav-admin-production-sync' in page.text
    assert setup.client.get('/production/performance/external',follow_redirects=False).status_code==303
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions='{}')
    assert setup.client.get('/api/production/external-sync/credentials').status_code==403
    assert setup.client.get('/api/production/external-sync/records').status_code==403
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'u','password':'p'}).status_code==403


def test_credentials_cannot_change_during_sync_and_test_does_not_save(setup,monkeypatch):
    from models.production_sync import ProductionSyncCredential
    monkeypatch.setattr(api_module,'ProductionClient',lambda *_:None)
    assert setup.client.post('/api/production/external-sync/credentials/test',json={'username':'u','password':'p'}).status_code==200
    with setup.sessions() as db:assert db.get(ProductionSyncCredential,1) is None
    lease(setup)
    assert setup.client.put('/api/production/external-sync/credentials',json={'username':'u','password':'p'}).status_code==409
    with setup.sessions() as db:assert db.get(ProductionSyncCredential,1) is None


def test_general_inquiry_includes_external_bom_mapping_total_and_generated_lot_for_regular_user(setup):
    token, _ = lease(setup)
    sync.save_day([row('LX20260325016', ITEM_NUM='310061', PROC_TYPE_NM='캡조립')], token)
    with setup.sessions() as db:
        db.add(ProcessModel(process_code='ASSY', process_name='조립', created_at='2026-01-01'))
        parent = db.get(ItemMasterModel, add_item(db, part='310061'))
        parent.account_type = 'PROD'; parent.material_type = 'FINISHED'; db.commit()
        child = db.get(ItemMasterModel, add_item(db, part='310062-C', code='ASSY'))
        add_bom(db, parent, child)
    setup.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(username='reader', role='USER', permissions='{}')
    reply = setup.client.get('/api/production/performance-status', params={'part_no':'310062-C','performance_type':'ASSEMBLY','process_code':'ASSY'}).json()
    assert len(reply) == 1
    record = reply[0]
    assert record['part_no'] == '310062-C' and record['total_qty'] == 331
    assert record['good_qty'] == 216 and record['defect_qty'] == 0 and record['setup_qty'] == 115
    assert record['output_lot_no'] == 'LX20260325016' and record['equipment_name'] == '1호기'
    assert record['can_delete'] is False and record['record_source'] == 'EXTERNAL'
    assert record['id'].startswith('external:') and '외부 연동' in record['note']
    assert setup.client.get('/api/production/performance-status',params={'part_no':'310061'}).json()[0]['id'] == record['id']
    with setup.sessions() as db:
        assert db.query(ProductionPerformance).count() == 0 and db.query(ProductionWorkOrder).count() == 0


def test_combined_inquiry_filters_and_total_are_independent_of_material_consumption(setup):
    from models.production_lot import ProductionLotModel
    token, _ = lease(setup)
    sync.save_day([row('LX20260325016', PROC_TYPE_NM='복합선반')], token)
    with setup.sessions() as db:
        db.add(ProcessModel(process_code='LT', process_name='복합선반', created_at='2026-01-01'))
        item_id = add_item(db, part='EXTERNAL-A')
        order = ProductionWorkOrder(work_order_no='TEST-WORK-001', order_date=DAY.isoformat(),item_id=item_id,part_no='EXTERNAL-A',order_qty=1000)
        db.add(order); db.flush()
        perf = ProductionPerformance(work_order_id=order.id,performance_type='MACHINING',performance_date=DAY.isoformat(),process_code='LT',operator_name='Alice',good_qty=656,defect_qty=2,setup_qty=10,consumed_qty=1312)
        db.add(perf); db.flush()
        db.add(ProductionLotModel(lot_no='LX-NATIVE-001', item_id=item_id,part_no='EXTERNAL-A',lot_qty=656,status='ACTIVE',note=f'PERF:{perf.id}|생산실적 자동생성'))
        db.commit()
    rows = setup.client.get('/api/production/performance-status').json()
    assert len(rows) == 2
    native = next(r for r in rows if r['record_source']=='MES')
    assert native['total_qty'] == 668 and native['consumed_qty'] == 1312
    assert native['output_lot_no'] == 'LX-NATIVE-001' and native['can_delete']
    assert sum(r['total_qty'] for r in rows) == 999
    for params in [{'work_order_no':'TEST-WORK'}, {'operator_name':'Alice'}]:
        filtered = setup.client.get('/api/production/performance-status', params=params).json()
        assert len(filtered)==1 and filtered[0]['record_source']=='MES'
    assert len(setup.client.get('/api/production/performance-status',params={'process_code':'LT','performance_type':'MACHINING'}).json())==2
    assert setup.client.get('/api/production/performance-status',params={'performance_type':'ASSEMBLY'}).json()==[]
    assert setup.client.get('/api/production/performance-status',params={'start_date':(DAY+timedelta(days=1)).isoformat()}).json()==[]
    assert setup.client.get('/api/production/performance-status',params={'part_no':'EXTERNAL_A'}).json()==[]
    with setup.sessions() as db:
        assert db.query(ProductionLotModel).count()==1 and db.query(ProductionPerformance).count()==1
        assert db.get(ProductionWorkOrder,order.id).production_qty==0


def test_unknown_setup_does_not_become_zero_total_and_source_lot_total_is_not_substituted(setup):
    token, _ = lease(setup)
    sync.save_day([row(LOT_QTY='999')], token)
    assert setup.client.get('/api/production/performance-status').json()[0]['total_qty']==331
    with setup.sessions() as db:
        record=db.query(ExternalProductionRecord).one()
        raw=json.loads(record.raw_json);raw.pop('F10');record.raw_json=json.dumps(raw);db.commit()
    record=setup.client.get('/api/production/performance-status').json()[0]
    assert record['total_qty'] is None and record['setup_qty'] is None
    assert '셋업 수량 누락' in record['note']


def test_packing_connection_uses_actual_finished_material_code(setup):
    token, _ = lease(setup)
    sync.save_day([row(ITEM_NUM='310062-C',PROC_TYPE_NM='포장')], token)
    with setup.sessions() as db:
        parent=db.get(ItemMasterModel,add_item(db,part='310061'))
        parent.account_type='PROD';parent.material_type='FINISHED';db.commit()
        child=db.get(ItemMasterModel,add_item(db,part='310062-C'))
        add_bom(db,parent,child)
    assert setup.client.get('/api/production/external-sync/item-maps').json()[0]['part_no']=='310061'
