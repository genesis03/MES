import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import models
from core.database import Base, get_db
from core.security import get_current_user
from models.models import ItemMasterModel, ItemBomModel, UserModel
from models.partner import Partner
from models.sales import SalesOrderMaster, SalesOrderItem
from models.production import ProductionWorkOrder
from models.production_lot import ProductionLotModel
from models.packing import PackingMaster, PackingBox
from routers import production_pipeline as routes
from services.production_pipeline_service import pipeline_data, fifo_allocate


@pytest.fixture
def context():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db=sessionmaker(bind=engine)()
    item=ItemMasterModel(part_no='FIN',part_name='완제품',account_type='PROD',material_type='FINISHED',created_at='2026-10-16')
    partner=Partner(partner_code='C1',partner_name='고객',partner_type='CUSTOMER')
    user=UserModel(username='pipeline',name='사용자',password_hash='x',role='ADMIN',created_at='2026-10-16')
    db.add_all([item,partner,user]);db.flush()
    master=SalesOrderMaster(order_no='SO-1',order_date='2026-10-01',customer_id=partner.id,customer_name='고객',status='ORDERED')
    master.items=[SalesOrderItem(item_id=item.id,part_no=item.part_no,order_qty=100,shipped_qty=0)]
    db.add(master)
    db.add(ProductionLotModel(lot_no='OLD',item_id=item.id,part_no=item.part_no,lot_qty=20,status='ACTIVE'))
    db.commit()
    yield db,item,master,user
    db.close();engine.dispose()


def test_100_demand_20_stock_no_mutations(context):
    db,item,master,user=context
    before=db.query(ProductionLotModel).one().lot_qty
    result=pipeline_data(db,user=user)
    row=result['items'][0];order=result['orders'][0]
    assert row['sales_order_qty']==100 and row['stock_allocated_qty']==20
    assert row['shipping_ready_rate']==20 and row['new_production_required_qty']==80
    assert order['allocations'][0]['lot_no']=='OLD'
    assert row['vision_inspection_qty'] is None
    assert db.query(ProductionLotModel).one().lot_qty==before
    assert pipeline_data(db,user=user)==result


def test_fifo_never_double_allocates_and_work_order_is_not_ready(context):
    db,item,master,user=context
    master.items[0].order_qty=10
    second=SalesOrderMaster(order_no='SO-2',order_date='2026-10-02',customer_id=master.customer_id,customer_name='고객')
    second.items=[SalesOrderItem(item_id=item.id,part_no=item.part_no,order_qty=100)]
    db.add(second)
    db.add(ProductionWorkOrder(work_order_no='WO-1',order_date='2026-10-01',item_id=item.id,part_no=item.part_no,order_qty=50,status='IN_PROGRESS'))
    db.commit()
    result=pipeline_data(db,user=user)
    first,second=result['orders']
    assert first['stock_allocated_qty']==10
    assert second['stock_allocated_qty']==10
    assert second['production_mapped_qty']==50
    assert second['new_production_required_qty']==90 and second['unmapped_qty']==40
    assert second['shipping_ready_rate']==10
    assert sum(a['qty'] for o in result['orders'] for a in o['allocations'] if a['kind']=='STOCK')==20


def test_packing_balance_counted_once_and_cancelled_orders_excluded(context):
    db,item,master,user=context
    packing=PackingMaster(packing_no='PK-1',packing_date='2026-10-01',item_id=item.id,part_no=item.part_no,
        box_count=1,box_qty=30,total_qty=30,status='PACKED')
    packing.boxes=[PackingBox(box_no=1,package_lot_no='PACK-001',box_qty=30)]
    db.add(packing);db.commit()
    row=pipeline_data(db,user=user)['items'][0]
    assert row['stock_allocated_qty']==20 and row['packed_allocated_qty']==30
    assert row['shipping_ready_rate']==50 and row['new_production_required_qty']==50
    packing.status='CANCELLED';db.commit()
    assert pipeline_data(db,user=user)['items'][0]['packed_ready_qty']==0
    master.status='CANCELLED';db.commit()
    assert pipeline_data(db,user=user)['items']==[]


def test_bom_identity_not_product_name_and_orphans_ignored(context):
    db,item,master,user=context
    child=ItemMasterModel(part_no='CHILD-A',part_name='같은 이름',account_type='PROD',material_type='SEMI',created_at='2026-10-16')
    orphan=ItemMasterModel(part_no='ORPHAN-A',part_name=item.part_name,account_type='PROD',material_type='SEMI',created_at='2026-10-16')
    db.add_all([child,orphan]);db.flush()
    db.add(ItemBomModel(parent_item_id=item.id,child_item_id=child.id,parent_part_no=item.part_no,child_part_no=child.part_no,quantity=1,created_at='2026-10-16'))
    db.add_all([ProductionLotModel(lot_no='CHILD-LOT',item_id=child.id,part_no=child.part_no,lot_qty=35),
                ProductionLotModel(lot_no='ORPHAN-LOT',item_id=orphan.id,part_no=orphan.part_no,lot_qty=999)])
    db.commit()
    assert pipeline_data(db,user=user)['items'][0]['machining_qty']==35


def test_api_access_and_page(context,monkeypatch):
    db,item,master,user=context
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[get_current_user]=lambda:user
    client=TestClient(app)
    assert client.get('/api/production/pipeline-data').json()['items'][0]['shipping_ready_rate']==20
    assert client.get('/production/pipeline-dashboard').status_code==200
    monkeypatch.setattr(routes,'DEV_BYPASS_AUTH',False)
    user.role='USER';user.permissions='{}'
    assert client.get('/api/production/pipeline-data').status_code==403


def test_pure_fifo_stable_order_and_item_isolation():
    orders=[{'item_id':1,'remaining_qty':10},{'item_id':2,'remaining_qty':10},{'item_id':1,'remaining_qty':10}]
    supplies=[{'item_id':1,'qty':15,'kind':'STOCK','lot_no':'FIRST'}, {'item_id':2,'qty':3,'kind':'PACKED','lot_no':'SECOND'}]
    result=fifo_allocate(orders,supplies)
    assert [r['shipping_ready_rate'] for r in result]==[100,30,50]
    assert supplies[0]['qty']==15
