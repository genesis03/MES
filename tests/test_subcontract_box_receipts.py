from concurrent.futures import ThreadPoolExecutor
import pytest
from test_production_sync import setup,DAY
from test_subcontract_dispatch_sync import row
from test_subcontract_external_receipt import masters,seed,prepare,BASE
from models.production_lot import ProductionLotModel
from models.lot_relation import LotRelationModel
from models.subcontract_inbound import SubcontractInboundMaster
from models.subcontract_dispatch_sync import ExternalSubcontractReceiptSource as Link
from routers.internal_labels import _subcontract_inbound_labels


def source(setup,native=False,second=False):
    masters(setup)
    rows=[row(ETC_REQ_QTY='1000')]
    if second:rows.append(row(RULE_NO='second',LOT_NUM='LS002',ETC_REQ_QTY='500'))
    result=prepare(setup,seed(setup,rows))
    if native:
        with setup.sessions() as db:db.query(Link).delete();db.commit()
    return result


def boxes(source,quantities=(250,250,250,250),source_index=0):
    lot=source['items'][0]['lots'][source_index]
    return dict(box_receipt=True,outbound_id=source['outbound_id'],inbound_date=DAY.isoformat(),storage_location='S-AG',lot_results=[dict(outbound_lot_id=lot['outbound_lot_id'],inbound_qty=qty,supplier_lot_no=f'SUP-BOX-{index+1}',sample_qty=0) for index,qty in enumerate(quantities)])


def edit_payload(receipt,quantities):
    return dict(inbound_date=DAY.isoformat(),storage_location='S-AG',lots=[dict(id=lot['id'],inbound_qty=qty,supplier_lot_no=lot['supplier_lot_no'],sample_qty=0) for lot,qty in zip(receipt['items'][0]['lots'],quantities)])


@pytest.mark.parametrize('native',[False,True])
def test_four_boxes_create_four_lots_and_four_correct_labels(setup,native):
    src=source(setup,native=native)
    result=setup.client.post(BASE,json=boxes(src));assert result.status_code==200,result.text
    receipt=result.json();lots=receipt['items'][0]['lots']
    assert len(lots)==4 and len({l['child_lot_no'] for l in lots})==4
    assert all(l['child_lot_no'].startswith('LZ') and l['inbound_qty']==250 for l in lots)
    assert len({l['outbound_lot_id'] for l in lots})==1
    source_after=setup.client.get(f"{BASE}/outbound/{src['outbound_id']}").json()
    assert source_after['total_received_qty']==1000 and source_after['total_remaining_qty']==0
    with setup.sessions() as db:
        assert sum(r.lot_qty for r in db.query(ProductionLotModel))==1000
        assert db.query(LotRelationModel).filter_by(parent_lot_no='LS001').count()==4
        labels=_subcontract_inbound_labels(db,receipt['id'])
        assert len(labels)==4 and all(label['qty']==250 for label in labels)
        assert {label['lot_no'] for label in labels}=={lot['child_lot_no'] for lot in lots}


def test_combined_box_quantities_cannot_exceed_source_remaining(setup):
    src=source(setup)
    rejected=setup.client.post(BASE,json=boxes(src,(600,600)));assert rejected.status_code==422,rejected.text
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0 and db.query(SubcontractInboundMaster).count()==0
    accepted=setup.client.post(BASE,json=boxes(src,(200,300)));assert accepted.status_code==200,accepted.text
    rejected=setup.client.post(BASE,json=boxes(src,(300,300)));assert rejected.status_code==422,rejected.text
    with setup.sessions() as db:assert sum(l.lot_qty for l in db.query(ProductionLotModel))==500


def test_box_totals_are_used_for_fifo_not_last_box_only(setup):
    src=source(setup,second=True)
    data=boxes(src,(500,500));data['lot_results']+=boxes(src,(250,),source_index=1)['lot_results']
    result=setup.client.post(BASE,json=data);assert result.status_code==200,result.text
    assert len(result.json()['items'][0]['lots'])==3


def test_cannot_skip_source_lot_with_multiple_boxes(setup):
    src=source(setup,second=True)
    data=boxes(src,(100,100));data['lot_results']+=boxes(src,(100,),source_index=1)['lot_results']
    rejected=setup.client.post(BASE,json=data);assert rejected.status_code==409,rejected.text
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0


def test_box_history_edit_validates_total_and_updates_each_child_lot(setup):
    src=source(setup);received=setup.client.post(BASE,json=boxes(src)).json()
    endpoint=f"/api/subcontract/inbound-edit/{received['id']}"
    rejected=setup.client.put(endpoint,json=edit_payload(received,(300,300,300,300)));assert rejected.status_code==422,rejected.text
    edited=setup.client.put(endpoint,json=edit_payload(received,(200,300,250,250)));assert edited.status_code==200,edited.text
    with setup.sessions() as db:
        assert sorted(r.lot_qty for r in db.query(ProductionLotModel))==[200,250,250,300]
        assert sorted(r.consumed_qty for r in db.query(LotRelationModel))==[200,250,250,300]
    history=setup.client.get(f"{BASE}/{received['id']}").json()
    assert len(history['items'][0]['lots'])==4 and history['items'][0]['inbound_qty']==1000
    duplicated=edit_payload(history,(200,300,250,250));duplicated['lots'].append(duplicated['lots'][0])
    assert setup.client.put(endpoint,json=duplicated).status_code==422


def test_cancel_removes_every_box_and_reopens_source_quantity(setup):
    src=source(setup);received=setup.client.post(BASE,json=boxes(src)).json()
    canceled=setup.client.post(f"{BASE}/{received['id']}/cancel");assert canceled.status_code==200,canceled.text
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0 and db.query(LotRelationModel).count()==0
    remaining=setup.client.get(f"{BASE}/outbound/{src['outbound_id']}").json();assert remaining['total_remaining_qty']==1000
    assert setup.client.post(BASE,json=boxes(src,(400,600))).status_code==200


@pytest.mark.parametrize('native',[False,True])
def test_concurrent_box_receipts_do_not_over_receive(setup,native):
    src=source(setup,native=native)
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:setup.client.post(BASE,json=boxes(src,(300,300))),range(2)))
    assert sorted(r.status_code for r in results)==[200,422]
    with setup.sessions() as db:assert sum(l.lot_qty for l in db.query(ProductionLotModel))==600


def test_missing_supplier_or_excess_sample_rejects_all_boxes(setup):
    src=source(setup);data=boxes(src)
    data['lot_results'][2]['sample_qty']=251
    assert setup.client.post(BASE,json=data).status_code==422
    data=boxes(src);data['lot_results'][2]['supplier_lot_no']=''
    assert setup.client.post(BASE,json=data).status_code==422
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0


def test_single_full_native_box_also_gets_its_own_new_lot(setup):
    src=source(setup,native=True)
    response=setup.client.post(BASE,json=boxes(src,(1000,)));assert response.status_code==200,response.text
    assert response.json()['items'][0]['lots'][0]['child_lot_no']!='LS001'


def test_lot_number_exhaustion_rolls_back_all_boxes(setup):
    src=source(setup)
    rejected=setup.client.post(BASE,json=boxes(src,(10,)*100));assert rejected.status_code==409,rejected.text
    with setup.sessions() as db:assert db.query(ProductionLotModel).count()==0 and db.query(SubcontractInboundMaster).count()==0
    remaining=setup.client.get(f"{BASE}/outbound/{src['outbound_id']}").json();assert remaining['total_remaining_qty']==1000


def test_redistributing_boxes_preserves_fifo_when_later_dispatch_is_received(setup):
    from models.subcontract_dispatch_sync import ExternalSubcontractDispatch as Record
    masters(setup)
    seed(setup,[row(REQ_NUM='R001',RULE_NO='first',LOT_NUM='LS001',ETC_REQ_QTY='1000'),row(REQ_NUM='R002',RULE_NO='second',LOT_NUM='LS002',ETC_REQ_QTY='500')])
    with setup.sessions() as db:ids={r.source_key:r.id for r in db.query(Record)}
    first=prepare(setup,ids['first']);receipt=setup.client.post(BASE,json=boxes(first)).json()
    second=prepare(setup,ids['second']);later=setup.client.post(BASE,json=boxes(second,(250,)));assert later.status_code==200,later.text
    endpoint=f"/api/subcontract/inbound-edit/{receipt['id']}"
    redistributed=setup.client.put(endpoint,json=edit_payload(receipt,(200,300,250,250)));assert redistributed.status_code==200,redistributed.text
    reduced=setup.client.put(endpoint,json=edit_payload(receipt,(200,250,250,250)));assert reduced.status_code==409,reduced.text
