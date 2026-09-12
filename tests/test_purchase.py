import os
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker

# models/__init__.py creates tables on import: never point it at production.
_bootstrap = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = 'sqlite:///' + str(Path(_bootstrap.name) / 'bootstrap.db')
from core.database import Base, engine as bootstrap_engine
from core.security import get_current_user
from models.models import ItemMasterModel, PurchaseOrderMaster, PurchaseOrderItem, PurchaseInboundMaster, PurchaseInboundItem
from models.partner import Partner
from routers.purchase import api_router, router, get_purchase_db


@pytest.fixture(scope='session', autouse=True)
def cleanup_bootstrap():
    yield
    bootstrap_engine.dispose()
    _bootstrap.cleanup()


@pytest.fixture
def setup(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'test.db'), connect_args={"check_same_thread": False, "timeout": 30})
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add_all([Partner(id=1, partner_code='V1', partner_name='공급사'), Partner(id=2, partner_code='V2', partner_name='다른공급사')])
        db.add_all([ItemMasterModel(part_no=p, part_name=p, account_type='RM', material_type='RM', created_at='2026-09-11') for p in ['A', 'B']])
        db.commit()
    app = FastAPI()
    app.include_router(router)
    app.include_router(api_router)
    def sessions():
        with factory() as db:
            yield db
    app.dependency_overrides[get_purchase_db] = sessions
    app.dependency_overrides[get_current_user] = lambda: {'username': 'tester'}
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def order(client, qty=10, **changes):
    body = dict(order_date='2026-09-11', partner_id=1, partner_name='공급사', items=[{'part_no': 'A', 'order_qty': qty}])
    body.update(changes)
    return client.post('/api/purchase/orders', json=body)


def inbound(client, po_item_id=None, qty=1, **changes):
    body = dict(inbound_date='2026-09-11', partner_id=1, partner_name='공급사', items=[dict(part_no='A', po_item_id=po_item_id, inbound_qty=qty, supplier_lot_no='SUP-001')])
    body.update(changes)
    return client.post('/api/purchase/inbound', json=body)


def test_partial_and_complete(setup):
    c, factory = setup
    r = order(c, items=[{'part_no': 'A', 'order_qty': 10}, {'part_no': 'B', 'order_qty': 5}])
    assert r.status_code == 201, r.text
    a, b = r.json()['items']
    assert inbound(c, a['id'], 3).status_code == 201
    with factory() as db:
        assert db.get(PurchaseOrderItem, a['id']).status == 'PARTIAL'
        assert db.get(PurchaseOrderMaster, r.json()['id']).status == 'PARTIAL'
    assert inbound(c, a['id'], 7).status_code == 201
    remaining = c.get('/api/purchase/orders/unreceived').json()
    assert remaining['total'] == 1 and remaining['items'][0]['remaining_qty'] == 5
    assert inbound(c, items=[dict(part_no='B', po_item_id=b['id'], inbound_qty=5, supplier_lot_no='SUP-B')]).status_code == 201
    with factory() as db:
        assert db.get(PurchaseOrderMaster, r.json()['id']).status == 'COMPLETED'
    assert c.get('/api/purchase/orders/unreceived').json()['total'] == 0


def test_inline_order_entry_and_vendor_code_search(setup):
    client, factory = setup
    with factory() as db:
        db.get(Partner, 1).partner_code = 'V-SEARCH'
        db.get(Partner, 2).partner_type = 'CUSTOMER'
        db.commit()
    found = client.get('/api/purchase/vendors/search', params={'keyword': 'V-SEARCH'})
    assert found.status_code == 200
    assert [row['id'] for row in found.json()['items']] == [1]
    assert client.get('/api/purchase/vendors/search', params={'keyword': '다른공급사'}).json()['total'] == 0

    response = order(client, manager_name='구매담당', delivery_due_date='2026-09-20',
                     items=[{'part_no': 'A', 'order_qty': 10, 'delivery_date': '2026-09-18', 'note': '먼저 납품'},
                            {'part_no': 'B', 'order_qty': 5, 'delivery_date': '2026-09-25', 'note': '나중 납품'}])
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['manager_name'] == '구매담당'
    assert [(item['delivery_date'], item['note']) for item in data['items']] == [
        ('2026-09-18', '먼저 납품'), ('2026-09-25', '나중 납품')]
    assert all(item['unit_price'] == 0 for item in data['items'])
    assert order(client, items=[{'part_no': 'A', 'order_qty': 1, 'delivery_date': '2026-02-30'}]).status_code == 422


def test_atomic_rollback(setup):
    c, factory = setup
    item = order(c).json()['items'][0]['id']
    rows = [dict(part_no='A', po_item_id=p, inbound_qty=2, supplier_lot_no='LOT') for p in [item, 99999]]
    assert inbound(c, items=rows).status_code == 404
    with factory() as db:
        assert db.get(PurchaseOrderItem, item).received_qty == 0
        assert db.scalar(select(func.count()).select_from(PurchaseInboundMaster)) == 0
        assert db.scalar(select(func.count()).select_from(PurchaseInboundItem)) == 0
    assert inbound(c, item).json()['inbound_no'].endswith('-001')


@pytest.mark.parametrize('field,value', [('inbound_qty', 0), ('inbound_qty', -1), ('supplier_lot_no', ''), ('supplier_lot_no', '  '), ('supplier_lot_no', None), ('unit_price', -1), ('part_no', ' '), ('warehouse_code', 'x'*21)])
def test_invalid_inbound(setup, field, value):
    c, _ = setup
    row = dict(part_no='A', inbound_qty=1, supplier_lot_no='LOT')
    row[field] = value
    assert inbound(c, items=[row]).status_code == 422


@pytest.mark.parametrize('change', [dict(items=[]), dict(order_date='2026-02-30'), dict(order_date='20260911'), dict(items=[dict(part_no='A', order_qty=-1)]), dict(items=[dict(part_no='A', order_qty=1, received_qty=2)])])
def test_invalid_order(setup, change):
    assert order(setup[0], **change).status_code == 422


def test_mismatched_links_and_cancelled(setup):
    c, factory = setup
    result = order(c).json()
    item = result['items'][0]['id']
    assert inbound(c, item, partner_id=2, partner_name='다른공급사').status_code == 422
    assert inbound(c, items=[dict(part_no='B', po_item_id=item, inbound_qty=1, supplier_lot_no='L')]).status_code == 422
    with factory() as db:
        db.get(PurchaseOrderMaster, result['id']).status = 'CANCELLED'
        db.commit()
    assert inbound(c, item).status_code == 409
    assert c.get('/api/purchase/orders/unreceived').json()['total'] == 0


def test_standalone_history_filters_and_audit(setup):
    c, _ = setup
    r = inbound(c, created_by='spoofed', invoice_no='INV-1')
    assert r.status_code == 201, r.text
    data = r.json()
    assert data['created_by'] == 'tester'
    assert data['items'][0]['po_item_id'] is None
    assert data['items'][0]['internal_lot_no'].startswith('LOT-IN-')
    assert data['items'][0]['warehouse_code'] == 'RM'
    assert data['items'][0]['storage_location'] == 'S-LT'
    history = c.get('/api/purchase/inbound/history', params=dict(start_date='2026-09-11', end_date='2026-09-11', partner_id=1, supplier_lot_no='SUP-001')).json()
    assert history['total'] == 1 and history['items'][0]['invoice_no'] == 'INV-1'
    assert c.get('/api/purchase/inbound/history', params={'partner_id': 2}).json()['total'] == 0
    assert c.get('/api/purchase/inbound/history', params={'partner_name': '%'}).json()['total'] == 0
    assert c.get('/api/purchase/inbound/history', params={'start_date':'2026-09-12','end_date':'2026-09-11'}).status_code == 422


def test_split_lots_decimal_and_overreceipt(setup):
    c, factory = setup
    result = order(c, qty=0.3).json()
    item = result['items'][0]['id']
    rows = [dict(part_no='A', po_item_id=item, inbound_qty=q, supplier_lot_no=f'L-{n}') for n, q in enumerate([0.1, 0.2])]
    assert inbound(c, items=rows).status_code == 201
    with factory() as db:
        assert db.get(PurchaseOrderItem, item).received_qty == 0.3
        assert db.get(PurchaseOrderMaster, result['id']).status == 'COMPLETED'
    assert inbound(c, item, 0.1).status_code == 201
    with factory() as db:
        assert db.get(PurchaseOrderItem, item).received_qty == 0.4


def test_concurrent_numbering_and_receipts(setup):
    c, factory = setup
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: order(c), range(12)))
    assert all(r.status_code == 201 for r in responses), [r.text for r in responses]
    assert len({r.json()['po_no'] for r in responses}) == 12
    item = responses[0].json()['items'][0]['id']
    with ThreadPoolExecutor(max_workers=6) as pool:
        receipts = list(pool.map(lambda _: inbound(c, item), range(10)))
    assert all(r.status_code == 201 for r in receipts), [r.text for r in receipts]
    assert len({r.json()['inbound_no'] for r in receipts}) == 10
    with factory() as db:
        assert db.get(PurchaseOrderItem, item).received_qty == 10
        assert db.get(PurchaseOrderItem, item).order.status == 'COMPLETED'


def test_number_over_999_and_today(setup):
    c, factory = setup
    prefix = f'PO-{datetime.now():%Y%m%d}-'
    with factory() as db:
        db.add_all([PurchaseOrderMaster(po_no=prefix+str(n), order_date='2020-01-01', partner_name='공급사') for n in [999, 1000]])
        db.commit()
    assert order(c, order_date='2020-01-01').json()['po_no'] == prefix+'1001'


def test_missing_masters_and_pagination(setup):
    c, _ = setup
    assert order(c, partner_id=999).status_code == 404
    assert order(c, items=[dict(part_no='missing', order_qty=1)]).status_code == 404
    assert inbound(c, items=[dict(part_no='missing', inbound_qty=1, supplier_lot_no='L')]).status_code == 404
    for _ in range(3):
        assert order(c).status_code == 201
    page = c.get('/api/purchase/orders/unreceived?offset=1&limit=1').json()
    assert page['total'] == 3 and len(page['items']) == 1


def test_auth_required(setup):
    from fastapi import HTTPException
    c, _ = setup
    def unauthenticated():
        raise HTTPException(401, '로그인이 필요합니다.')
    c.app.dependency_overrides[get_current_user] = unauthenticated
    assert order(c).status_code == 401
    assert inbound(c).status_code == 401
    assert c.get('/api/purchase/orders/unreceived').status_code == 401
    assert c.get('/api/purchase/inbound/history').status_code == 401


def test_legacy_routes_retained(setup):
    paths = set(setup[0].app.openapi()['paths'])
    assert {'/purchase', '/purchase/api', '/purchase/api/list', '/purchase/api/{purchase_id}'}.issubset(paths)


def test_multiple_orders_one_receipt(setup):
    c, factory = setup
    first, second = order(c, qty=2).json(), order(c, qty=3).json()
    rows = [dict(part_no='A', po_item_id=o['items'][0]['id'], inbound_qty=q, supplier_lot_no='L') for o, q in [(first, 2), (second, 1)]]
    assert inbound(c, items=rows).status_code == 201
    with factory() as db:
        assert db.get(PurchaseOrderMaster, first['id']).status == 'COMPLETED'
        assert db.get(PurchaseOrderMaster, second['id']).status == 'PARTIAL'


def test_nonfinite_numbers_rejected(setup):
    c, _ = setup
    body = '{"order_date":"2026-09-11","partner_name":"supplier","items":[{"part_no":"A","order_qty":"Infinity"}]}'
    assert c.post('/api/purchase/orders', content=body, headers={'Content-Type':'application/json'}).status_code == 422


def test_constraint_failure_rolls_back(setup):
    from schemas.purchase import InboundCreate
    from services.purchase_service import create_inbound
    from fastapi import HTTPException
    c, factory = setup
    item = order(c).json()['items'][0]['id']
    @event.listens_for(PurchaseInboundItem, 'before_insert')
    def invalid_insert(mapper, connection, target):
        target.inbound_qty = -1
    try:
        with factory() as db:
            payload = InboundCreate(inbound_date='2026-09-11', partner_id=1, partner_name='공급사', items=[dict(part_no='A', po_item_id=item, inbound_qty=1, supplier_lot_no='L')])
            with pytest.raises(HTTPException) as error:
                create_inbound(db, payload, 'tester')
            assert error.value.status_code == 409
        with factory() as db:
            assert db.get(PurchaseOrderItem, item).received_qty == 0
            assert db.scalar(select(func.count()).select_from(PurchaseInboundMaster)) == 0
    finally:
        event.remove(PurchaseInboundItem, 'before_insert', invalid_insert)

