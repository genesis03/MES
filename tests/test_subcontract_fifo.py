from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models
from core.database import Base
from models.models import ItemMasterModel, StorageLocationModel
from models.production_lot import ProductionLotModel
from models.subcontract_outbound import SubcontractOutboundMaster, SubcontractOutboundItem, SubcontractOutboundLot
from models.subcontract_inbound import SubcontractInboundMaster, SubcontractInboundItem, SubcontractInboundLot
from services.subcontract_fifo import sort_source_lots, validate_outbound_selection, validate_inbound_selection
from routers import subcontract, subcontract_outbound, subcontract_inbound_lot_policy
from schemas.subcontract import LotAllocationInput


@pytest.fixture
def db():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def dispatch(db, number, day, lots, partner=1, process='PL', item=2):
    master = SubcontractOutboundMaster(outbound_no=number, outbound_date=day, order_id=1,
        order_no='SO', partner_id=partner, partner_name='업체', processing_type_code=process,
        processing_type_name='도금', external_storage_location='OUT', status='OUTBOUND')
    detail = SubcontractOutboundItem(order_item_id=1, previous_item_id=1, item_id=item,
        previous_part_no='RAW', order_part_no='PLATED', order_part_name='제품', unit='EA',
        outbound_qty=sum(lots.values()), lot_count=len(lots))
    detail.lots = [SubcontractOutboundLot(lot_no=lot, outbound_qty=qty) for lot, qty in lots.items()]
    master.items = [detail]
    db.add(master); db.commit()
    return master


def receive(db, outbound, quantities, status='RECEIVED'):
    receipt = SubcontractInboundMaster(inbound_no=f'I{db.query(SubcontractInboundMaster).count()}',
        inbound_date='2026-10-30',outbound_id=outbound.id,outbound_no=outbound.outbound_no,
        order_id=1,order_no='SO',partner_id=outbound.partner_id,partner_name='업체',
        processing_type_code=outbound.processing_type_code,processing_type_name='도금',
        storage_location='IN',status=status)
    item=SubcontractInboundItem(outbound_item_id=outbound.items[0].id,order_item_id=1,
        previous_item_id=1,item_id=outbound.items[0].item_id,previous_part_no='RAW',part_no='PLATED',
        part_name='제품',unit='EA',outbound_qty=sum(quantities.values()),good_qty=sum(quantities.values()))
    item.lots=[SubcontractInboundLot(outbound_lot_id=lot_id,source_lot_no='SOURCE',source_qty=100,
                                   good_qty=qty,defect_qty=0) for lot_id,qty in quantities.items()]
    receipt.items=[item];db.add(receipt);db.commit()
    return receipt


def test_production_date_beats_late_registration(db):
    db.add_all([ProductionLotModel(lot_no='LX20261001001',item_id=1,part_no='RAW',lot_qty=100,
                                  created_at=datetime(2026,10,30)),
                ProductionLotModel(lot_no='LX20261002001',item_id=1,part_no='RAW',lot_qty=100,
                                   created_at=datetime(2026,10,2))]);db.commit()
    rows=sort_source_lots(db,[{'lot_no':'LX20261002001'},{'lot_no':'LX20261001001'}])
    assert [row['fifo_date'] for row in rows]==['2026-10-01','2026-10-02']
    with pytest.raises(HTTPException): validate_outbound_selection(rows,['LX20261002001'])
    validate_outbound_selection(rows,['LX20261001001'])
    validate_outbound_selection(rows,['LX20261002001','LX20261001001'])


def test_later_dispatch_blocked_until_earlier_fully_received(db):
    old=dispatch(db,'O1','2026-10-01',{'A':100})
    new=dispatch(db,'O2','2026-10-02',{'B':100})
    a=old.items[0].lots[0].id;b=new.items[0].lots[0].id
    with pytest.raises(HTTPException): validate_inbound_selection(db,new,{b:50})
    receive(db,old,{a:80})
    with pytest.raises(HTTPException): validate_inbound_selection(db,new,{b:50})
    validate_inbound_selection(db,old,{a:20})
    receive(db,old,{a:20})
    validate_inbound_selection(db,new,{b:50})


def test_partial_receipt_cannot_skip_lot_within_same_dispatch(db):
    out=dispatch(db,'O1','2026-10-01',{'A':100,'B':100})
    a,b=[lot.id for lot in out.items[0].lots]
    with pytest.raises(HTTPException):validate_inbound_selection(db,out,{a:50,b:50})
    validate_inbound_selection(db,out,{a:100,b:50})
    validate_inbound_selection(db,out,{a:50})


@pytest.mark.parametrize('kwargs',[{'partner':2},{'process':'CNC'},{'item':3}])
def test_other_partner_process_or_part_does_not_block(db,kwargs):
    dispatch(db,'O1','2026-10-01',{'A':100},**kwargs)
    out=dispatch(db,'O2','2026-10-02',{'B':100})
    validate_inbound_selection(db,out,{out.items[0].lots[0].id:50})


def test_cancelled_dispatch_and_cancelled_receipts(db):
    old=dispatch(db,'O1','2026-10-01',{'A':100})
    new=dispatch(db,'O2','2026-10-02',{'B':100})
    a=old.items[0].lots[0].id;b=new.items[0].lots[0].id
    receive(db,old,{a:100},status='CANCELLED')
    with pytest.raises(HTTPException):validate_inbound_selection(db,new,{b:50})
    old.status='CANCELLED';db.commit()
    validate_inbound_selection(db,new,{b:50})


def test_edit_excludes_own_receipt(db):
    out=dispatch(db,'O1','2026-10-01',{'A':100,'B':100})
    a,b=[lot.id for lot in out.items[0].lots]
    receipt=receive(db,out,{a:100,b:50})
    validate_inbound_selection(db,out,{a:100,b:40},exclude_inbound_id=receipt.id)
    with pytest.raises(HTTPException):validate_inbound_selection(db,out,{a:50,b:40},exclude_inbound_id=receipt.id)


def make_order(db, number, lot_no, day='2026-10-01'):
    from models.subcontract import SubcontractOrderMaster, SubcontractOrderItem, SubcontractLotAllocation
    master=SubcontractOrderMaster(order_no=number,order_date=day,partner_id=1,partner_name='업체',
        processing_type_code='PL',processing_type_name='도금',external_storage_location='OUT',status='ORDERED')
    item=SubcontractOrderItem(previous_item_id=1,item_id=2,previous_part_no='RAW',order_part_no='PLATED',
        order_part_name='제품',unit='EA',processing_type_code='PL',processing_type_name='도금',order_qty=100)
    item.allocations=[SubcontractLotAllocation(lot_no=lot_no,lot_qty=100,allocated_qty=100)]
    master.items=[item];db.add(master);db.commit()
    return master


def stock(db):
    db.add_all([ItemMasterModel(id=1,part_no='RAW',part_name='가공품',account_type='PROD',
                               material_type='SEMI',created_at='2026-10-01'),
                ItemMasterModel(id=2,part_no='PLATED',part_name='도금품',account_type='PROD',
                               material_type='SEMI',created_at='2026-10-01'),
                StorageLocationModel(location_code='IN',location_name='입고',is_active='Y',created_at='2026-10-01'),
                ProductionLotModel(lot_no='LX20261001001',item_id=1,part_no='RAW',lot_qty=100),
                ProductionLotModel(lot_no='LX20261002001',item_id=1,part_no='RAW',lot_qty=100)])
    db.commit()


def test_outbound_final_check_blocks_bypassing_reserved_earlier_lot(db):
    stock(db)
    old=make_order(db,'SO1','LX20261001001')
    new=make_order(db,'SO2','LX20261002001')
    with pytest.raises(HTTPException) as exc:
        subcontract_outbound.create_outbound(subcontract_outbound.OutboundCreateInput(order_id=new.id,outbound_date='2026-10-30'),db,None)
    assert 'LX20261001001' in exc.value.detail
    assert db.query(SubcontractOutboundMaster).count()==0
    subcontract_outbound.create_outbound(subcontract_outbound.OutboundCreateInput(order_id=old.id,outbound_date='2026-10-30'),db,None)
    subcontract_outbound.create_outbound(subcontract_outbound.OutboundCreateInput(order_id=new.id,outbound_date='2026-10-30'),db,None)
    assert db.query(SubcontractOutboundMaster).count()==2


def test_assignment_and_confirmation_reject_skip(db):
    stock(db)
    order=make_order(db,'SO1','LX20261002001');order.status='DRAFT';db.commit()
    item=order.items[0]
    with pytest.raises(HTTPException):
        subcontract.set_subcontract_lots(order.id,item.id,LotAllocationInput(lot_nos=['LX20261002001']),db,None)
    with pytest.raises(HTTPException):subcontract.confirm_subcontract_order(order.id,db,None)
    assert item.allocations[0].lot_no=='LX20261002001'
    subcontract.set_subcontract_lots(order.id,item.id,LotAllocationInput(lot_nos=['LX20261001001']),db,None)
    subcontract.confirm_subcontract_order(order.id,db,None)
    assert order.status=='ORDERED'


def test_actual_inbound_route_rejects_skip_before_writes(db):
    from routers.subcontract_inbound import InboundCreateInput
    stock(db)
    old=dispatch(db,'O1','2026-10-01',{'LX20261001001':100})
    new=dispatch(db,'O2','2026-10-02',{'LX20261002001':100})
    payload=InboundCreateInput(outbound_id=new.id,inbound_date='2026-10-30',storage_location='IN',
        lot_results=[{'outbound_lot_id':new.items[0].lots[0].id,'inbound_qty':50,'supplier_lot_no':'SUP'}])
    with pytest.raises(HTTPException):subcontract_inbound_lot_policy.create_inbound_with_lot_policy(payload,db,None)
    assert db.query(SubcontractInboundMaster).count()==0
    payload.outbound_id=old.id;payload.lot_results[0].outbound_lot_id=old.items[0].lots[0].id
    result=subcontract_inbound_lot_policy.create_inbound_with_lot_policy(payload,db,None)
    assert result['items'][0]['lots'][0]['inbound_qty']==50
    assert db.query(SubcontractInboundMaster).count()==1


def test_reduction_and_cancel_blocked_after_later_receipt(db):
    from services.subcontract_fifo import validate_receipt_reduction
    from routers.subcontract_inbound import cancel_inbound
    old=dispatch(db,'O1','2026-10-01',{'A':100})
    new=dispatch(db,'O2','2026-10-02',{'B':100})
    receipt=receive(db,old,{old.items[0].lots[0].id:100})
    validate_receipt_reduction(db,receipt,{})
    later=receive(db,new,{new.items[0].lots[0].id:50})
    with pytest.raises(HTTPException):validate_receipt_reduction(db,receipt,{})
    with pytest.raises(HTTPException):cancel_inbound(receipt.id,db,None)
    assert receipt.status=='RECEIVED'
    validate_receipt_reduction(db,later,{})


def test_performance_date_and_parent_lineage_override_registration(db):
    from models.production import ProductionPerformance
    from models.lot_relation import LotRelationModel
    performance=ProductionPerformance(work_order_id=1,performance_type='MACHINING',
        performance_date='2026-09-01',process_code='LT',good_qty=100)
    db.add(performance);db.flush()
    db.add_all([
        ProductionLotModel(lot_no='LX20261001001',item_id=1,part_no='RAW',lot_qty=100,
                           note=f'PERF:{performance.id}|생산실적'),
        ProductionLotModel(lot_no='LZ20261030001',item_id=2,part_no='PLATED',lot_qty=100),
        LotRelationModel(parent_lot_no='LX20261001001',child_lot_no='LZ20261030001',consumed_qty=100)])
    db.commit()
    result=sort_source_lots(db,[{'lot_no':'LZ20261030001'}])
    assert result[0]['fifo_date']=='2026-09-01'


def test_unknown_production_date_does_not_silently_use_registration_date(db):
    db.add(ProductionLotModel(lot_no='MANUAL',item_id=1,part_no='RAW',lot_qty=100));db.commit()
    with pytest.raises(HTTPException) as exc:sort_source_lots(db,[{'lot_no':'MANUAL'}])
    assert '생산일' in exc.value.detail


def test_same_part_multiple_lines_checked_as_one_dispatch(db):
    from models.subcontract import SubcontractOrderItem, SubcontractLotAllocation
    stock(db)
    order=make_order(db,'SO1','LX20261001001')
    second=SubcontractOrderItem(previous_item_id=1,item_id=2,previous_part_no='RAW',order_part_no='PLATED',
        order_part_name='제품',unit='EA',processing_type_code='PL',processing_type_name='도금',order_qty=100)
    second.allocations=[SubcontractLotAllocation(lot_no='LX20261002001',lot_qty=100,allocated_qty=100)]
    order.items.append(second);db.commit()
    result=subcontract_outbound.create_outbound(
        subcontract_outbound.OutboundCreateInput(order_id=order.id,outbound_date='2026-10-30'),db,None)
    assert len(result['items'])==2
