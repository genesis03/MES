import pytest
from fastapi import HTTPException
from test_inventory_ledger import env
from models.partner import Partner
from models.models import ItemBomModel
from models.production_lot import ProductionLotModel
from models.sales import SalesOrderMaster, SalesOrderItem, ShipmentMaster, ShipmentDirectLot
from models.packing import PackingMaster, PackingBox
from models.lot_relation import LotRelationModel
from routers import sales_order_policy as orders
from routers import sales_shipping_direct as direct
from routers import sales_shipping_partial_confirm as packed
from routers.packing import _lot_available


def make_order(env,kind,transaction='FREE'):
    db=env.db
    order=SalesOrderMaster(order_no='SO1',order_date='2026-11-02',customer_id=1,customer_name='고객',order_type=kind,transaction_type=transaction)
    order.items=[SalesOrderItem(item_id=3,part_no='FIN',order_qty=20,shipped_qty=0,status='WAITING')]
    db.add(order);db.commit()
    return order


def test_replacement_can_be_created_updated_and_queried(env):
    env.client.app.include_router(orders.router)
    env.db.add(Partner(partner_code='CUSTOMER',partner_name='고객',partner_type='CUSTOMER',is_active='Y'));env.db.commit()
    partner=env.db.query(Partner).one()
    payload={'order_date':'2026-11-02','customer_id':partner.id,'order_type':'REPLACEMENT','transaction_type':'FREE','items':[{'part_no':'FIN','order_qty':20}]}
    response=env.client.post('/api/sales/orders',json=payload)
    assert response.status_code==200,response.text
    order=env.db.get(SalesOrderMaster,response.json()['id'])
    assert order.order_type=='REPLACEMENT' and order.transaction_type=='FREE'
    payload['transaction_type']='PAID'
    response=env.client.put(f'/api/sales/orders/{order.id}',json=payload)
    assert response.status_code==200,response.text
    assert env.db.get(SalesOrderMaster,order.id).order_type=='REPLACEMENT'
    assert env.db.get(SalesOrderMaster,order.id).transaction_type=='PAID'


@pytest.mark.parametrize('transaction',['PAID','FREE'])
@pytest.mark.parametrize('kind',['SAMPLE','DEVELOPMENT','REPLACEMENT'])
def test_direct_dispatch_consumes_selected_wip_lot_without_packing(env,transaction,kind):
    db=env.db;order=make_order(env,kind,transaction)
    db.add(ItemBomModel(parent_item_id=3,child_item_id=2,parent_part_no='FIN',child_part_no='SEMI-A',bom_type='FINAL',created_at='2026-11-02'))
    lot=ProductionLotModel(lot_no='LX20261101001',item_id=2,part_no='SEMI-A',lot_qty=100,status='ACTIVE')
    db.add(lot);db.commit()
    payload=direct.DirectShipmentCreateInput(shipment_date='2026-11-02',items=[{'sales_order_item_id':order.items[0].id,'requested_qty':10,'direct_lots':[{'production_lot_id':lot.id,'qty':10}]}])
    assert _lot_available(db,lot)==100
    result=direct.direct_confirm(payload,db,env.state.user)
    assert result['shipment_mode']=='DIRECT'
    assert _lot_available(db,lot)==90
    assert db.get(ProductionLotModel,lot.id).lot_qty==100  # Original ledger quantity is preserved.
    assert db.query(PackingMaster).count()==0
    relation=db.query(LotRelationModel).one()
    assert relation.parent_lot_no==lot.lot_no and relation.consumed_qty==10
    trace=db.query(ShipmentDirectLot).one()
    assert trace.source_item_id==2 and trace.source_part_no=='SEMI-A'
    assert trace.outbound_lot_no


def test_normal_does_not_get_direct_dispatch_exception(env):
    order=make_order(env,'NORMAL')
    with pytest.raises(HTTPException) as exc:direct.direct_lots(order.items[0].id,env.db,env.state.user)
    assert exc.value.status_code==409


@pytest.mark.parametrize('kind',['NORMAL','SAMPLE','DEVELOPMENT','REPLACEMENT'])
def test_packed_fifo_exception_is_only_for_special_order_types(env,kind):
    db=env.db;order=make_order(env,kind)
    masters=[]
    for n in (1,2):
        master=PackingMaster(packing_no=f'P{n}',packing_date=f'2026-11-0{n}',item_id=3,part_no='FIN',box_count=1,box_qty=10,total_qty=10)
        master.boxes=[PackingBox(box_no=1,package_lot_no=f'26110{n}01001',box_qty=10)]
        masters.append(master);db.add(master)
    db.commit()
    payload=packed.ShipmentOrderCreateInput(shipment_date='2026-11-02',items=[{'sales_order_item_id':order.items[0].id,'packing_box_ids':[masters[1].boxes[0].id],'requested_qty':10}])
    if kind=='NORMAL':
        with pytest.raises(HTTPException) as exc:packed.confirm_shipment_with_requested_qty(payload,db,env.state.user)
        assert exc.value.status_code==409
        assert db.query(ShipmentMaster).count()==0
        payload.items[0].packing_box_ids=[masters[0].boxes[0].id]
    packed.confirm_shipment_with_requested_qty(payload,db,env.state.user)
    shipment=db.query(ShipmentMaster).one()
    assert shipment.fifo_exception==('N' if kind=='NORMAL' else 'Y')
    if kind=='REPLACEMENT':assert '대체품' in shipment.fifo_exception_reason


@pytest.mark.parametrize('kind',['NORMAL','REPLACEMENT'])
def test_same_part_and_date_rows_remain_separate_on_create_and_update(env,kind):
    env.client.app.include_router(orders.router)
    partner=Partner(partner_code='C',partner_name='고객',partner_type='CUSTOMER',is_active='Y');env.db.add(partner);env.db.commit()
    payload={'order_date':'2026-11-02','delivery_due_date':'2026-11-06','customer_id':partner.id,'order_type':kind,'transaction_type':'FREE',
             'items':[{'part_no':'FIN','order_qty':10,'delivery_date':'2026-11-06'},{'part_no':'FIN','order_qty':28,'delivery_date':'2026-11-06'}]}
    response=env.client.post('/api/sales/orders',json=payload);assert response.status_code==200,response.text
    order_id=response.json()['id']
    detail=env.client.get(f'/api/sales/orders/{order_id}').json()
    assert [r['order_qty'] for r in detail['items']]==[10,28]
    assert len({r['id'] for r in detail['items']})==2
    response=env.client.put(f'/api/sales/orders/{order_id}',json=payload);assert response.status_code==200,response.text
    detail=env.client.get(f'/api/sales/orders/{order_id}').json()
    assert [r['order_qty'] for r in detail['items']]==[10,28]
    assert len({r['id'] for r in detail['items']})==2


def test_duplicate_replacement_rows_can_share_source_lot_without_overconsumption(env):
    db=env.db;order=make_order(env,'REPLACEMENT')
    order.items[0].order_qty=10;order.items[0].delivery_date='2026-11-06'
    order.items.append(SalesOrderItem(item_id=3,part_no='FIN',order_qty=28,delivery_date='2026-11-06',status='WAITING'))
    db.add(ItemBomModel(parent_item_id=3,child_item_id=2,parent_part_no='FIN',child_part_no='SEMI-A',bom_type='FINAL',created_at='2026-11-02'))
    lot=ProductionLotModel(lot_no='LX20261101001',item_id=2,part_no='SEMI-A',lot_qty=100,status='ACTIVE');db.add(lot);db.commit()
    allocations=[{'sales_order_item_id':item.id,'requested_qty':qty,'direct_lots':[{'production_lot_id':lot.id,'qty':qty}]} for item,qty in zip(order.items,[10,28])]
    direct.direct_confirm(direct.DirectShipmentCreateInput(shipment_date='2026-11-06',items=allocations),db,env.state.user)
    assert _lot_available(db,lot)==62
    assert [item.shipped_qty for item in order.items]==[10,28]
    assert order.status=='COMPLETED'
    # Shipping history must still prohibit editing/rebuilding these line IDs.
    with pytest.raises(HTTPException):orders.update_sales_order_with_policy(order.id,orders.SalesOrderCreateInput(order_date='2026-11-02',customer_id=1,items=[{'part_no':'FIN','order_qty':38}]),db,env.state.user)


def test_duplicate_normal_rows_ship_distinct_fifo_boxes(env):
    db=env.db;order=make_order(env,'NORMAL');order.items[0].order_qty=10
    order.items.append(SalesOrderItem(item_id=3,part_no='FIN',order_qty=28,status='WAITING'))
    packs=[]
    for n,qty in enumerate([10,28],1):
        master=PackingMaster(packing_no=f'P{n}',packing_date=f'2026-11-0{n}',item_id=3,part_no='FIN',box_count=1,box_qty=qty,total_qty=qty)
        master.boxes=[PackingBox(box_no=1,package_lot_no=f'26110{n}01001',box_qty=qty)];packs.append(master);db.add(master)
    db.commit()
    payload=packed.ShipmentOrderCreateInput(shipment_date='2026-11-06',items=[{'sales_order_item_id':item.id,'packing_box_ids':[master.boxes[0].id],'requested_qty':qty} for item,master,qty in zip(order.items,packs,[10,28])])
    packed.confirm_shipment_with_requested_qty(payload,db,env.state.user)
    assert [item.shipped_qty for item in order.items]==[10,28]
    assert db.query(ShipmentMaster).one().fifo_exception=='N'
