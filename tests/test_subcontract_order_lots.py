import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import pytest
from services import subcontract_order_lots as detail
from services.subcontract_dispatch_client import parse_response
from services.production_sync_client import SyncError


def capture():return json.loads((Path(__file__).parent/'fixtures/subcontract_order_detail.json').read_text())


def rows():
    values=json.loads((Path(__file__).parent/'fixtures/subcontract_dispatch_list.json').read_text())
    result,_=parse_response(values,date(2026,10,1),date(2026,11,9))
    return [row for row in result if row['REQ_NUM']=='R202611073']


def test_captured_order_matches_all_66_actual_allocations_and_assigned_quantities():
    result=detail.parse_detail(capture(),rows());summary={}
    for row in result:
        children=row['_ORDER_LOTS'];detail.validate_allocations(row)
        summary[row['ITEM_NUM']]=(len(children),sum(Decimal(child['JOB_QTY']) for child in children))
        assert sum(Decimal(child['JOB_QTY']) for child in children)==Decimal(row['ETC_REQ_QTY'])
    assert summary=={'310196-A':(20,Decimal(3468)),'310203-A':(10,Decimal(1152)),'310199-A':(20,Decimal(3081)),'310062-B':(16,Decimal(2639))}
    boxes=[box for row in result for box in row['_ORDER_LOTS']]
    assert len(boxes)==len({box['LOT_NUM'] for box in boxes})==66
    assert sum(Decimal(box['JOB_QTY']) for box in boxes)==10340
    assert sum(Decimal(box['LOT_QTY']) for box in boxes)==10659
    assert boxes[0]['LOT_NUM'].startswith(('LX','LB'))


@pytest.mark.parametrize('field,value',[
 ('MES.Request.Set.ReqInfoSet.REQ_NUM','OTHER'),
 ('MES.Request.Set.ReqInfoSet.REQ_COMP_NO','OTHER'),
 ('MES.Request.Set.ReqInfoSet.ETC_REQ_TYPE','OTHER'),
 ('MES.Request.Set.ReqInfoSet.RULE_NO',''),
 ('Common.Set.LotSet.LOT_NUM','ONLY-ONE'),
 ('Common.Set.LotSet.JOB_QTY','NaN'),
 ('MONITOR.QUERY.END',''),
 ('SO.NET.END','bad')])
def test_invalid_wrong_document_or_truncated_detail_rejected(field,value):
    values=capture();values[field]=[value]
    with pytest.raises(SyncError):detail.parse_detail(values,rows())


@pytest.mark.parametrize('field', ['RULE_NO','LOT_NUM'])
def test_duplicate_allocation_lot_rejected(field):
    values=capture();key=detail.LOTS+field;cells=values[key][0].split(',');cells[1]=cells[0];values[key]=[','.join(cells)]
    with pytest.raises(SyncError,match='중복'):detail.parse_detail(values,rows())


def test_missing_box_or_wrong_assigned_quantity_rejected():
    values=capture();key=detail.LOTS+'JOB_QTY';cells=values[key][0].split(',');cells[0]=str(Decimal(cells[0])-1);values[key]=[','.join(cells)]
    with pytest.raises(SyncError,match='합계'):detail.parse_detail(values,rows())


def test_common_lot_item_cannot_be_attached_to_an_unrelated_order_line():
    values=capture();key=detail.LOTS+'ITEM_NO';cells=values[key][0].split(',');cells[0]='unrelated';values[key]=[','.join(cells)]
    with pytest.raises(SyncError,match='속하지'):detail.parse_detail(values,rows())


def test_aggregate_detail_line_quantity_must_match_outbound_list():
    source=rows();source[0]['ETC_REQ_QTY']='1'
    with pytest.raises(SyncError,match='출고수량'):detail.parse_detail(capture(),source)


def test_detail_request_uses_master_key_not_outbound_detail_line_key(monkeypatch):
    from services import production_sync_client as source
    values=capture();key=values[detail.HEADER+'RULE_NO'][0];calls=[]
    monkeypatch.setattr(source,'client_common',lambda _:{'ENCRYPT.KEY':'current'})
    monkeypatch.setattr(source,'query_with_retry',lambda opener,payload:(calls.append(payload) or values))
    client=SimpleNamespace(context={},opener=object())
    result=detail.fetch_detail(client,rows(),key)
    assert len(result)==4 and len(calls)==1
    assert calls[0][detail.HEADER+'RULE_NO']==key
    assert key not in {row['RULE_NO'] for row in rows()}
    assert calls[0]['run.object.name']=='MES.Request.Etc.EtcReqObj'
    assert calls[0]['jump.form.code']=='List'
    assert calls[0]['ENCRYPT.KEY']=='current'
    assert calls[0][detail.HEADER+'REQ_NUM']=='R202611073'
    with pytest.raises(SyncError):detail.fetch_detail(client,rows(),None)
    assert len(calls)==1


def header_capture():return json.loads((Path(__file__).parent/'fixtures/subcontract_order_header_check.json').read_text())


@pytest.mark.parametrize('field,value',[
 ('ROW_COUNT','0'),('ROW_COUNT','2'),('REQ_NUM','OTHER'),('REQ_COMP_NO','OTHER'),('ETC_REQ_TYPE','OTHER'),('STAT_CODE','REQ_REG'),('RULE_NO','')])
def test_header_lookup_rejects_missing_ambiguous_or_wrong_order(monkeypatch,field,value):
    from services import production_sync_client as source
    values=header_capture();values[detail.HEADER+field]=[value]
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),rows())


def test_complete_observed_list_check_detail_chain_returns_66_lots(monkeypatch):
    from services import subcontract_dispatch_client as dispatch,production_sync_client as source
    from test_subcontract_dispatch_sync import response
    instance=dispatch.SubcontractDispatchClient.__new__(dispatch.SubcontractDispatchClient)
    instance.context={};instance.opener=object();instance.encrypt_key='4321';calls=[]
    def query(opener,payload):
        calls.append(payload)
        obj=payload['run.object.name']
        if obj=='MES.Request.EtcReqDetailList.EtcReqDetailListObj':return response(rows())
        if obj=='Common.Pop.Request.EtcReqPopObj':return header_capture()
        if obj=='MES.Request.Etc.EtcReqObj':return capture()
        raise AssertionError(obj)
    monkeypatch.setattr(dispatch,'query_with_retry',query);monkeypatch.setattr(source,'query_with_retry',query)
    result,notes=instance.fetch_range(date(2026,11,7),date(2026,11,7))
    assert len(result)==4 and sum(len(row['_ORDER_LOTS']) for row in result)==66
    assert [p['jump.form.code'] for p in calls]==['List','CHECK','List']
    assert all(p['ENCRYPT.KEY']=='4321' for p in calls)
    assert calls[1][detail.CONDITION+'REQ_NUM']=='R202611073'
    assert calls[1][detail.CONDITION+'FIELD_RULE_NO']=='MES.Request.Etc.EtcReqForm.ReqInfoModel.RULE_NO'
    assert calls[2][detail.HEADER+'RULE_NO']==header_capture()[detail.HEADER+'RULE_NO'][0]
    assert calls[2][detail.HEADER+'RULE_NO'] not in {row['RULE_NO'] for row in rows()}
    assert '개별 발주 LOT 66개 확인' in notes


def test_order_lookup_is_not_restricted_to_dispatch_date(monkeypatch):
    from services import production_sync_client as source
    values=header_capture();values[detail.HEADER+'REQ_DATE']=['2026-01-01 오전 12:00:00'];calls=[]
    monkeypatch.setattr(source,'query_with_retry',lambda opener,payload:(calls.append(dict(payload)) or values))
    client=SimpleNamespace(context={},opener=object(),encrypt_key='4321')
    key=detail.lookup_header(client,rows())
    assert key==values[detail.HEADER+'RULE_NO'][0]
    assert calls[0][detail.CONDITION+'REQ_DATE1']==calls[0][detail.CONDITION+'REQ_DATE2']==''
    assert client.order_lookup_headers[key]['REQ_DATE']=='2026-01-01'
    assert calls[0][detail.CONDITION+'REQ_NUM']=='R202611073'


def test_matching_scalar_header_can_be_verified_without_row_count(monkeypatch):
    from services import production_sync_client as source
    values=header_capture();values.pop(detail.HEADER+'ROW_COUNT')
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    key=detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),rows())
    assert key==values[detail.HEADER+'RULE_NO'][0]


def test_missing_count_does_not_allow_missing_identity(monkeypatch):
    from services import production_sync_client as source
    values=header_capture();values.pop(detail.HEADER+'ROW_COUNT');values[detail.HEADER+'RULE_NO']=['']
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),rows())


def test_empty_type_filtered_result_retries_same_number_without_type_filter(monkeypatch):
    from services import production_sync_client as source
    calls=[];empty=header_capture();empty[detail.HEADER+'ROW_COUNT']=['0']
    def query(opener,payload):
        calls.append(dict(payload));return empty if payload[detail.CONDITION+'ETC_REQ_TYPE'] else header_capture()
    monkeypatch.setattr(source,'query_with_retry',query)
    key=detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),rows())
    assert key==header_capture()[detail.HEADER+'RULE_NO'][0] and len(calls)==2
    assert calls[1][detail.CONDITION+'ETC_REQ_TYPE']==''
    assert all(p[detail.CONDITION+'REQ_NUM']=='R202611073' for p in calls)


@pytest.mark.parametrize('count,expected',[('0','0건'),('2','2건'),('invalid','유효하지')])
def test_header_count_error_explains_returned_result(monkeypatch,count,expected):
    from services import production_sync_client as source
    values=header_capture();values[detail.HEADER+'ROW_COUNT']=[count]
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError,match=expected):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),rows())


def test_detail_request_uses_actual_order_date_from_lookup(monkeypatch):
    from services import production_sync_client as source
    values=header_capture();values[detail.HEADER+'REQ_DATE']=['2026-01-01 오전 12:00:00'];calls=[]
    def query(opener,payload):
        calls.append(dict(payload));return values if payload['jump.form.code']=='CHECK' else capture()
    monkeypatch.setattr(source,'query_with_retry',query)
    client=SimpleNamespace(context={},opener=object(),encrypt_key='4321')
    key=detail.lookup_header(client,rows());detail.fetch_detail(client,rows(),key)
    assert calls[1][detail.HEADER+'REQ_DATE']=='2026-01-01'
