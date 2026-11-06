from datetime import datetime
import pytest
from test_purchase import setup, inbound
from models.inbound_sync import ExternalInboundRecord
from models.models import PurchaseInboundMaster
from models.production_lot import ProductionLotModel

BASE='/api/purchase/inquiry/external-inbounds'


def seed(factory,**changes):
    now=datetime.now()
    with factory() as db:
        row=ExternalInboundRecord(source_key='external-1',inbound_no='I202609111',history_no='I202609111A1',part_no='A',inbound_date='2026-09-11',confirm_date='2026-09-12',partner_name='외부 공급사',confirmed_qty='200',product_type='원자재',material_type='동소재',raw_json='{}',content_hash='digest',first_seen_at=now,last_seen_at=now,changed_at=now,**changes)
        db.add(row);db.commit();return row.id


def test_external_inquiry_does_not_enter_native_picker_or_change_stock(setup):
    client,factory=setup
    response=inbound(client);assert response.status_code==201,response.text
    native=response.json()
    seed(factory)
    with factory() as db:before=(db.query(PurchaseInboundMaster).count(),db.query(ProductionLotModel).count())
    data=client.get(BASE).json()
    assert data['total']==1
    row=data['items'][0]
    assert row['source_type']=='EXTERNAL_INBOUND' and row['readonly'] is True
    assert row['inbound_id']<0 and row['inbound_id']!=native['id']
    assert row['inbound_qty']=='200' and row['part_name']=='A' and row['unit']=='EA'
    assert row['internal_lot_no']==row['supplier_lot_no']==''
    assert client.get('/api/purchase/inquiry/inbounds').json()['total']==1
    with factory() as db:assert (db.query(PurchaseInboundMaster).count(),db.query(ProductionLotModel).count())==before


@pytest.mark.parametrize('params',[{'status':'DRAFT'},{'part_no':'NOPE'},{'partner_name':'NOPE'},{'inbound_no':'NONE'},{'lot':'L'},{'po_no':'P'},{'start_date':'2026-09-12'},{'end_date':'2026-09-10'}])
def test_filters_apply_to_external_rows(setup,params):
    client,factory=setup;seed(factory)
    response=client.get(BASE,params=params)
    assert response.status_code==200 and response.json()['total']==0


def test_pagination_filters_and_missing_rows(setup):
    client,factory=setup;identifier=seed(factory)
    with factory() as db:
        original=db.get(ExternalInboundRecord,identifier)
        for i in range(1,4):
            values={c.name:getattr(original,c.name) for c in ExternalInboundRecord.__table__.columns if c.name!='id'}
            values.update(source_key=f'external-{i+1}',part_no=f'YR31031{i}',present=i!=3)
            db.add(ExternalInboundRecord(**values))
        db.commit()
    first=client.get(BASE+'?limit=1').json();second=client.get(BASE+'?offset=1&limit=1').json()
    assert first['total']==second['total']==3
    assert first['items'][0]['inbound_id']!=second['items'][0]['inbound_id']
    assert client.get(BASE+'?part_no=31031&partner_name=공급&status=CONFIRMED').json()['total']==2
    unknown=client.get(BASE+'?part_no=31031').json()['items'][0]
    assert unknown['unit']==unknown['part_name']==''
    for params in ({'status':'BAD'},{'start_date':'bad'},{'start_date':'2026-09-12','end_date':'2026-09-11'}):assert client.get(BASE,params=params).status_code==422

