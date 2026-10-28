from datetime import timedelta
from test_production_sync import setup
from test_shipment_sync import DAY, record, register
from models.models import ItemMasterModel
from services import production_sync_service as sync
from services.shipping_inquiry_parts import build_part_report


def seed(setup):
    with setup.sessions() as db:
        items=[ItemMasterModel(part_no=part,part_name='MES '+part,account_type='PROD',
            material_type='FINISHED',is_active='Y',created_at='2026-10-28',updated_at='2026-10-28')
            for part in ('310194-1','YR310231')]
        db.add_all(items);db.commit();ids=[i.id for i in items]
    token,_=sync.claim(DAY,DAY+timedelta(days=1),'MANUAL','shipment')
    first=record();sync.save_day([first],token,'shipment',queried_day=DAY)
    other=record();other['header']=other['header']|{'RULE_NO':'SECOND','OUT_NUM':'SECOND',
        'OUT_DATE':(DAY+timedelta(days=1)).isoformat()}
    sync.save_day([other],token,'shipment',queried_day=DAY+timedelta(days=1))
    return ids


def test_selected_part_report_counts_only_selected_quantity_over_entire_period(setup):
    register(setup);ids=seed(setup)
    result=setup.client.get('/api/shipping/inquiry',params={'view':'parts','item_id':ids[0],'limit':1})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['total']==2 and data['truncated']
    assert len(data['items'])==1
    assert data['items'][0]['part_no']=='310194-1'
    assert data['items'][0]['total_qty']==2040 and data['items'][0]['total_boxes']==17
    assert data['totals']=={'shipped_qty':4080,'box_count':34,'shipment_count':2}
    assert data['summary']==[{'part_no':'310194-1','part_name':'MES 310194-1',
        'shipped_qty':4080,'box_count':34,'shipment_count':2}]
    # Existing document lookups still get the complete document and its three parts.
    documents=setup.client.get('/api/shipping/inquiry',params={'item_id':ids[0]}).json()['items']
    assert len(documents)==2 and documents[0]['total_qty']==3040 and len(documents[0]['items'])==3


def test_multiple_parts_filters_period_source_and_document_number(setup):
    register(setup);ids=seed(setup)
    params=[('view','parts'),('item_id',ids[0]),('item_id',ids[1])]
    data=setup.client.get('/api/shipping/inquiry',params=params).json()
    assert len(data['summary'])==2 and data['total']==4
    assert data['totals']['shipped_qty']==4640 and data['totals']['shipment_count']==2
    one=setup.client.get('/api/shipping/inquiry',params=params+[('end_date',DAY.isoformat())]).json()
    assert one['total']==2 and one['totals']['shipped_qty']==2320
    assert setup.client.get('/api/shipping/inquiry',params=params+[('source','INTERNAL')]).json()['summary']==[]
    one=setup.client.get('/api/shipping/inquiry',params=params+[('shipment_no','SECOND')]).json()
    assert one['totals']['shipment_count']==1 and one['total']==2
    all_parts=setup.client.get('/api/shipping/inquiry?view=parts').json()
    assert len(all_parts['summary'])==3 and all_parts['totals']['shipped_qty']==6080


def test_same_part_in_two_order_lines_is_one_document_row_and_shared_lot_is_one_box():
    shipment=dict(id=1,shipment_no='NATIVE',shipment_date='2026-10-28',items=[
        dict(item_id=1,part_no='PART',part_name='Current name',shipped_qty=10,
             boxes=[{'waiting_lot_no':'SAME-BOX'}]),
        dict(item_id=1,part_no='PART',part_name='Current name',shipped_qty=5,
             direct_lots=[{'outbound_lot_no':'SAME-BOX'}]),
        dict(item_id=2,part_no='OTHER',shipped_qty=100,boxes=[])])
    data=build_part_report([shipment],[1],1000)
    assert data['total']==1 and data['items'][0]['total_qty']==15
    assert data['items'][0]['total_boxes']==1
    assert data['summary'][0]['shipment_count']==1


def test_internal_and_external_same_date_quantity_are_not_deduplicated():
    native=dict(id=1,shipment_no='NATIVE',shipment_date='2026-10-28',items=[
        dict(item_id=1,part_no='PART',shipped_qty=10,boxes=[{'waiting_lot_no':'LOT'}])])
    external=dict(native,id=-1,source='EXTERNAL',shipment_no='EXTERNAL')
    report=build_part_report([native,external],[],1000)
    assert report['total']==2 and report['totals']=={'shipped_qty':20,'box_count':2,'shipment_count':2}


def test_native_report_combines_same_part_order_lines_and_uses_current_master_name(setup):
    from models.partner import Partner
    from models.sales import SalesOrderMaster,SalesOrderItem,ShipmentMaster,ShipmentItem,ShipmentBox
    from models.packing import PackingMaster,PackingBox
    register(setup)
    with setup.sessions() as db:
        partner=Partner(partner_code='P',partner_name='Customer')
        item=ItemMasterModel(part_no='NATIVE',part_name='Current master name',account_type='PROD',
            material_type='FINISHED',is_active='Y',created_at='2026-10-28',updated_at='2026-10-28')
        db.add_all([partner,item]);db.flush()
        order=SalesOrderMaster(order_no='O1',order_date=DAY.isoformat(),customer_id=partner.id,customer_name='Customer')
        db.add(order);db.flush()
        packing=PackingMaster(packing_no='P1',packing_date=DAY.isoformat(),item_id=item.id,part_no=item.part_no,
            box_count=2,box_qty=10,total_qty=30)
        shipment=ShipmentMaster(shipment_no='S1',shipment_date=DAY.isoformat(),sales_order_id=order.id,
            customer_id=partner.id,customer_name='Customer')
        db.add_all([packing,shipment]);db.flush()
        for index,qty in enumerate((10,20),1):
            sales_item=SalesOrderItem(order_id=order.id,item_id=item.id,part_no=item.part_no,
                part_name='Old transaction name',order_qty=100,shipped_qty=qty)
            box=PackingBox(packing_id=packing.id,box_no=index,package_lot_no=f'LOT{index}',box_qty=qty)
            db.add_all([sales_item,box]);db.flush()
            ship_item=ShipmentItem(shipment_id=shipment.id,sales_order_item_id=sales_item.id,
                item_id=item.id,part_no=item.part_no,shipped_qty=qty)
            db.add(ship_item);db.flush()
            db.add(ShipmentBox(shipment_item_id=ship_item.id,packing_box_id=box.id,
                package_lot_no=box.package_lot_no,shipped_qty=qty))
        db.commit()
    data=setup.client.get('/api/shipping/inquiry?view=parts&source=INTERNAL').json()
    assert data['total']==1 and data['items'][0]['total_qty']==30
    assert data['items'][0]['total_boxes']==2
    assert data['summary'][0]['part_name']=='Current master name'
    assert data['summary'][0]['shipment_count']==1
