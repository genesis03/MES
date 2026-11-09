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


def test_problem_order_r202611072_has_verified_header_and_original_lot():
    values=json.loads((Path(__file__).parent/'fixtures/subcontract_order_detail_r202611072.json').read_text())
    listing=json.loads((Path(__file__).parent/'fixtures/subcontract_dispatch_list.json').read_text())
    source,_=parse_response(listing,date(2026,10,1),date(2026,11,9))
    source=[record for record in source if record['REQ_NUM']=='R202611072']
    result=detail.parse_detail(values,source)
    assert len(result)==1
    assert result[0]['_ORDER_RULE_NO']=='2026110714570260400012102562628'
    assert result[0]['ITEM_NUM']=='YR310284-D'
    assert result[0]['_ORDER_LOTS'][0]['LOT_NUM']=='LZ20261107011'
    assert result[0]['_ORDER_LOTS'][0]['JOB_QTY']=='663'
    assert result[0]['_ORDER_LOTS'][0]['LOT_QTY']=='663'



def search_capture():return json.loads((Path(__file__).parent/'fixtures/subcontract_order_search.json').read_text())


def document_rows(number):
    listing=json.loads((Path(__file__).parent/'fixtures/subcontract_dispatch_list.json').read_text())
    source,_=parse_response(listing,date(2026,10,1),date(2026,11,9))
    return [record for record in source if record['REQ_NUM']==number]


@pytest.mark.parametrize('number,key',[('R202611072','2026110714570260400012102562628'),('R202611073','2026110715091438900011973976379')])
def test_search_selects_matching_row_from_actual_three_order_response(monkeypatch,number,key):
    from services import production_sync_client as source
    calls=[]
    monkeypatch.setattr(source,'query_with_retry',lambda opener,payload:(calls.append(dict(payload)) or search_capture()))
    client=SimpleNamespace(context={},opener=object(),encrypt_key='4321',order_search_range=(date(2026,11,2),date(2026,11,9)))
    assert detail.lookup_header(client,document_rows(number))==key
    assert calls[0]['jump.form.code']=='SEARCH'
    assert calls[0][detail.CONDITION+'REQ_NUM']==number
    assert calls[0][detail.CONDITION+'REQ_DATE1']=='2026-11-01'
    assert calls[0][detail.CONDITION+'REQ_DATE2']=='2026-11-10'
    assert client.order_lookup_headers[key]['REQ_DATE']==('2026-11-05' if number.endswith('72') else '2026-11-07')


@pytest.mark.parametrize('field,value',[
 ('RULE_NO','wrong-count'),('REQ_COMP_NO','wrong-count'),('ETC_REQ_TYPE','wrong-count'),('ROW_COUNT','1')])
def test_malformed_search_table_is_not_silently_indexed(monkeypatch,field,value):
    from services import production_sync_client as source
    values=search_capture();values[detail.HEADER+field]=[value]
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),document_rows('R202611072'))


@pytest.mark.parametrize('field,replacement',[('REQ_COMP_NO','OTHER'),('ETC_REQ_TYPE','PROC_TAB'),('STAT_CODE','REQ_FIN'),('RULE_NO','')])
def test_matching_order_number_still_requires_correct_identity(monkeypatch,field,replacement):
    from services import production_sync_client as source
    values=search_capture();cells=values[detail.HEADER+field][0].split(',');cells[2]=replacement;values[detail.HEADER+field]=[','.join(cells)]
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),document_rows('R202611072'))


def test_other_orders_in_search_result_cannot_supply_the_header(monkeypatch):
    from services import production_sync_client as source
    values=search_capture();values[detail.HEADER+'REQ_NUM']=['R202611071,R202611073,R-OTHER']
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError,match='요청한 발주번호가 없습니다'):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),document_rows('R202611072'))


def test_duplicate_matching_header_is_rejected(monkeypatch):
    from services import production_sync_client as source
    values=search_capture();values[detail.HEADER+'REQ_NUM']=['R202611071,R202611072,R202611072']
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    with pytest.raises(SyncError,match='2건'):detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),document_rows('R202611072'))


@pytest.mark.parametrize('number,lot_count,total',[('R202611072',1,663),('R202611073',66,10340)])
def test_actual_list_search_detail_chain_for_each_order(monkeypatch,number,lot_count,total):
    from services import subcontract_dispatch_client as dispatch,production_sync_client as source
    from test_subcontract_dispatch_sync import response
    calls=[];requested=document_rows(number)
    def query(opener,payload):
        calls.append(dict(payload));obj=payload['run.object.name']
        if obj=='MES.Request.EtcReqDetailList.EtcReqDetailListObj':return response(requested)
        if obj=='Common.Pop.Request.EtcReqPopObj':
            assert payload['jump.form.code']=='SEARCH'
            return search_capture()
        if obj=='MES.Request.Etc.EtcReqObj':
            return json.loads((Path(__file__).parent/'fixtures/subcontract_order_detail_r202611072.json').read_text()) if number.endswith('72') else capture()
        raise AssertionError(obj)
    monkeypatch.setattr(dispatch,'query_with_retry',query);monkeypatch.setattr(source,'query_with_retry',query)
    client=dispatch.SubcontractDispatchClient.__new__(dispatch.SubcontractDispatchClient);client.context={};client.opener=object();client.encrypt_key='4321'
    result,notes=client.fetch_range(date(2026,11,2),date(2026,11,9))
    children=[child for row in result for child in row['_ORDER_LOTS']]
    assert len(children)==lot_count and sum(Decimal(child['JOB_QTY']) for child in children)==total
    assert [p['jump.form.code'] for p in calls]==['List','SEARCH','List']
    assert all(p['ENCRYPT.KEY']=='4321' for p in calls)
    assert calls[2][detail.HEADER+'REQ_NUM']==number
    assert calls[2][detail.HEADER+'RULE_NO']==result[0]['_ORDER_RULE_NO']
    assert calls[2][detail.HEADER+'RULE_NO'] not in {row['RULE_NO'] for row in requested}
    assert f'개별 발주 LOT {lot_count}개 확인' in notes


def test_search_also_accepts_single_filtered_header(monkeypatch):
    from services import production_sync_client as source
    values=search_capture()
    for field in ('REQ_NUM','RULE_NO','REQ_COMP_NO','ETC_REQ_TYPE','STAT_CODE','REQ_DATE'):
        values[detail.HEADER+field]=[values[detail.HEADER+field][0].split(',')[2]]
    values[detail.HEADER+'ROW_COUNT']=['1']
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    assert detail.lookup_header(SimpleNamespace(context={},opener=object(),encrypt_key='4321'),document_rows('R202611072'))=='2026110714570260400012102562628'


def test_empty_search_does_not_reuse_a_previous_header(monkeypatch):
    from services import production_sync_client as source
    values=search_capture()
    for key in list(values):
        if key.startswith(detail.HEADER):values.pop(key)
    monkeypatch.setattr(source,'query_with_retry',lambda *_:values)
    client=SimpleNamespace(context={},opener=object(),encrypt_key='4321',order_lookup_headers={'old':{'REQ_DATE':'2026-11-07'}})
    with pytest.raises(SyncError,match='발주번호가 없습니다'):detail.lookup_header(client,document_rows('R202611072'))
