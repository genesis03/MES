"""Subcontract source snapshots never become production or stock transactions."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from test_production_sync import setup, DAY
from core.security import get_current_user
from models.subcontract_fault_sync import ExternalSubcontractFault, SubcontractFaultSyncState, SubcontractFaultSyncRun
from models.production_sync import ExternalProductionRecord
from models.production import ProductionPerformance
from models.production_lot import ProductionLotModel
from models.subcontract_inbound import SubcontractInboundMaster
from routers.subcontract_fault_sync import router, BASE
from services import production_sync_service as sync
from services import subcontract_fault_client as source
from services.production_sync_client import SyncError
from services.subcontract_defect_status import subcontract_defect_status


def row(**changes):
    return dict.fromkeys((*source.QUANTITY_FIELDS, *source.FAULT_FIELDS,
        'PRODUCT_NM', 'APPLY_COMP_NM', 'ORDER_COMP_NM', 'CUS_LOT_NUM'), '') | {
        'IN_NUM': 'IN-001', 'ITEM_NUM': 'PART', 'LOT_NUM': 'LOT-001',
        'PROC_TYPE_NM': '은도금', 'CONFIRM_DATE': DAY.isoformat(),
        'FAULT_QTY': '5', 'F1': '2', 'F11': '3', 'ORI_LOT_QTY': '100', 'JOB_QTY': '95'} | changes


def response(rows):
    return {source.PREFIX + k: [','.join(r[k] for r in rows)] for k in row()} | {
        'SO.NET.END': ['SO.NET.END'], 'log.invoke.object': ['MES.QC.EtcFaultList.test'],
        'MONITOR.QUERY.START': ['start'], 'MONITOR.QUERY.END': ['end']}


def save(rows, token):
    return sync.save_day(rows, token, 'subcontract', queried_day=DAY)


def test_parser_and_query_are_separate_from_production(monkeypatch):
    client = source.SubcontractFaultClient.__new__(source.SubcontractFaultClient)
    client.opener = object(); client.context = {}
    calls = []
    def query(_, payload):
        calls.append(payload)
        return response([row(), row(CONFIRM_DATE=(DAY+timedelta(days=1)).isoformat())])
    monkeypatch.setattr(source, 'query_with_retry', query)
    assert client.fetch_day(DAY) == ([row()], [])
    assert calls[0]['run.object.name'] == 'MES.QC.EtcFaultList.EtcFaultListObj'
    assert calls[0]['jump.form.code'] == 'List'
    assert calls[0]['MES.Product.Set.JobCondSet.JOB_DATE1'] == (DAY-timedelta(days=1)).isoformat()


@pytest.mark.parametrize('field,value', [('FAULT_QTY','NaN'), ('F1','-1'), ('IN_NUM',''),
    ('LOT_NUM',''), ('CONFIRM_DATE','2025-01-01'), ('JOB_QTY','Infinity')])
def test_invalid_rows_fail_before_storage(field, value):
    with pytest.raises(SyncError):
        source.parse_response(response([row(**{field:value})]), DAY, DAY)


def test_empty_and_incomplete_envelopes():
    assert source.parse_response(response([]), DAY, DAY) == ([], [])
    for key, value in [('SO.NET.END', ['']), ('log.invoke.object', ['Common.Login.test']),
                       ('jump.form.code', ['Login']), (source.PREFIX+'ROW_COUNT', ['2'])]:
        data = response([row()]); data[key] = value
        with pytest.raises(SyncError): source.parse_response(data, DAY, DAY)
    data = response([row(), row(IN_NUM='OTHER')]); data[source.PREFIX+'F1'] = ['2']
    with pytest.raises(SyncError): source.parse_response(data, DAY, DAY)


def test_repeat_import_removal_reappearance_and_isolation(setup):
    token, _ = sync.claim(DAY, DAY, 'MANUAL', 'subcontract')
    assert sync.claim(DAY, DAY, 'MANUAL', 'subcontract') is None
    assert sync.claim(DAY, DAY, 'MANUAL') is not None
    assert save([row(), row(IN_NUM='OTHER')], token)['inserted'] == 2
    assert save([row(), row(IN_NUM='OTHER')], token)['unchanged'] == 2
    assert save([row(FAULT_QTY='4', F11='2')], token)['updated'] == 2
    with setup.sessions() as db:
        assert db.query(ExternalSubcontractFault).filter_by(present=True).count() == 1
        for model in (ExternalProductionRecord, ProductionPerformance, ProductionLotModel, SubcontractInboundMaster):
            assert db.query(model).count() == 0
    with pytest.raises(SyncError): save([row(),row()], token)
    assert save([row(), row(IN_NUM='OTHER')], token)['updated'] == 2
    with setup.sessions() as db:
        state = db.get(SubcontractFaultSyncState,1); state.lease_until = sync.now()-timedelta(seconds=1); db.commit()
    with pytest.raises(SyncError): save([], token)
    with setup.sessions() as db:
        assert db.query(ExternalSubcontractFault).filter_by(present=True).count() == 2


def test_mapping_changes_existing_report_without_reimport_and_nullable_totals(setup):
    setup.app.include_router(router)
    token, _ = sync.claim(DAY, DAY, 'MANUAL', 'subcontract')
    save([row(), row(IN_NUM='ZERO', FAULT_QTY='0', F1='0', F11='0')],token)
    with setup.sessions() as db:
        report = subcontract_defect_status(db)
        assert report['total'] == 1 and report['totals']['defect_qty'] == 5
        assert report['totals']['inbound_qty'] is None
        assert all(v is None for v in report['totals']['by_type'].values())
    payload = {'defects': {'F1':'1','F11':'3'}, 'inbound_field':'ORI_LOT_QTY',
               'good_field':'INBOUND_MINUS_DEFECT', 'partner_field':''}
    assert setup.client.put(BASE+'/mapping',json=payload).status_code == 200
    with setup.sessions() as db:
        report = subcontract_defect_status(db)
        assert report['totals']['inbound_qty'] == 100 and report['totals']['good_qty'] == 95
        assert report['totals']['by_type']['1'] == 2 and report['totals']['by_type']['3'] == 3
        assert report['items'][0]['record_source'] == 'EXTERNAL_SUBCONTRACT'
        assert subcontract_defect_status(db, part_no='missing')['total'] == 0
    save([], token)
    with setup.sessions() as db:
        assert subcontract_defect_status(db)['total'] == 0


def test_routes_pagination_mapping_validation_and_admin_access(setup, monkeypatch):
    setup.app.include_router(router)
    token, _ = sync.claim(DAY, DAY, 'MANUAL', 'subcontract')
    save([row(IN_NUM=f'IN-{i}') for i in range(25)], token)
    first = setup.client.get(BASE+'/records').json()
    second = setup.client.get(BASE+'/records?page=2').json()
    assert first['total'] == 25 and len(first['rows']) == 20 and len(second['rows']) == 5
    assert not ({r['id'] for r in first['rows']} & {r['id'] for r in second['rows']})
    assert setup.client.get(BASE+'/records?keyword=IN-24').json()['total'] == 1
    assert setup.client.put(BASE+'/mapping',json={'defects':{'F1':'unknown'}}).status_code == 422
    assert setup.client.put(BASE+'/mapping',json={'good_field':'INBOUND_MINUS_DEFECT'}).status_code == 422
    assert setup.client.get('/admin/subcontract-fault-sync').status_code == 200
    setup.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role='USER',username='worker',permissions=None)
    for path in ('/settings','/records','/runs','/mapping'):
        assert setup.client.get(BASE+path).status_code == 403


def test_scheduler_dispatches_to_subcontract_backend(setup, monkeypatch):
    class Client:
        def __init__(self,*_): pass
        def fetch_day(self, day): return [row()], []
    monkeypatch.setattr(source, 'SubcontractFaultClient', Client)
    token, run_id = sync.claim(DAY,DAY,'MANUAL','subcontract')
    sync.execute(DAY,DAY,token,run_id,'subcontract')
    with setup.sessions() as db:
        run = db.get(SubcontractFaultSyncRun,run_id)
        assert run.status == 'SUCCESS', run.error
        assert json.loads(run.counts_json)['inserted'] == 1
        assert db.get(SubcontractFaultSyncState,1).lease_token is None


def test_native_duplicate_requires_receipt_identity_not_only_lot(setup):
    from services.subcontract_fault_view import merge_report
    from services.defect_report_format import report_types
    token, _ = sync.claim(DAY, DAY, 'MANUAL', 'subcontract')
    save([row()], token)
    with setup.sessions() as db:
        columns = report_types(db)
        native = {'date':DAY.isoformat(), 'inbound_no':'DIFFERENT', 'part_no':'PART',
            'lot_no':'LOT-001', 'process_name':'은도금', 'unallocated':False}
        def totals():
            return {'inbound_qty':10.,'good_qty':9.,'defect_qty':1.,'by_type':{c['code']:0. for c in columns}}
        args = (1,columns,None,None,'','',5000)
        combined = merge_report(db,[native.copy()],totals(),*args)
        assert combined['total'] == 2 and combined['totals']['defect_qty'] == 6
        native['inbound_no'] = 'IN-001'
        combined = merge_report(db,[native],totals(),*args)
        assert combined['total'] == 1 and combined['totals']['defect_qty'] == 1
