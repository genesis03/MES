"""Production deletion must preserve subsequent quality records, even with SQLite FK checks off."""
import pytest
from sqlalchemy import text
from test_production_sync import setup,DAY,add_item
from models.production import ProductionPerformance,ProductionWorkOrder
from models.production_lot import ProductionLotModel
from models.production_defect import QualityProductionDefect,QualityProductionDefectDetail


def records(setup,status='ACTIVE'):
    with setup.sessions() as db:
        # Reproduce the runtime database policy; correctness cannot depend on FK enforcement.
        db.execute(text('PRAGMA foreign_keys=OFF'))
        item=add_item(db,part='310062-C')
        order=ProductionWorkOrder(work_order_no='DEFECT-WORK',order_date=DAY.isoformat(),item_id=item,part_no='310062-C',order_qty=100,production_qty=100,status='COMPLETED')
        db.add(order);db.flush()
        performances=[]
        for index in range(2):
            perf=ProductionPerformance(work_order_id=order.id,performance_date=DAY.isoformat(),process_code='ASSY',performance_type='ASSEMBLY',good_qty=50,defect_qty=0,setup_qty=0)
            db.add(perf);db.flush()
            lot=ProductionLotModel(item_id=item,part_no='310062-C',lot_no=f'LA-DEFECT-{index}',lot_qty=50,status='ACTIVE',note=f'PERF:{perf.id}|생산실적 자동생성')
            db.add(lot);db.flush();performances.append(perf.id)
            if index==1:
                defect=QualityProductionDefect(production_lot_id=lot.id,item_id=item,lot_no=lot.lot_no,lot_qty=50,available_qty_before=50,defect_date=DAY.isoformat(),defect_qty=1,status=status)
                db.add(defect);db.flush()
                db.add(QualityProductionDefectDetail(defect_id=defect.id,defect_type_code='1',defect_qty=1))
        db.commit();return order.id,performances


@pytest.mark.parametrize('status',['ACTIVE','CANCELLED'])
@pytest.mark.parametrize('whole_order',[False,True])
def test_post_defect_history_blocks_single_and_whole_order_delete_atomically(setup,status,whole_order):
    order,performances=records(setup,status)
    url=f'/api/production/orders/{order}/super-delete' if whole_order else f'/api/production/performances/{performances[1]}'
    reply=setup.client.delete(url)
    assert reply.status_code==409,reply.text
    assert '사후 불량' in reply.json()['detail']
    with setup.sessions() as db:
        assert db.query(ProductionPerformance).count()==2
        assert db.query(ProductionLotModel).count()==2
        assert db.query(QualityProductionDefect).count()==1
        assert db.query(QualityProductionDefectDetail).count()==1
        assert db.get(ProductionWorkOrder,order).production_qty==100
    inquiry=setup.client.get('/api/production/performance-status').json()
    blocked=next(row for row in inquiry if row['id']==performances[1])
    assert blocked['post_defect_registered'] and '사후 불량' in blocked['delete_block_reason']
    assert not next(row for row in inquiry if row['id']==performances[0])['post_defect_registered']


def test_production_without_post_defect_remains_deletable(setup):
    order,performances=records(setup)
    reply=setup.client.delete(f'/api/production/performances/{performances[0]}')
    assert reply.status_code==200,reply.text
    with setup.sessions() as db:
        assert db.query(ProductionPerformance).count()==1
        assert db.query(ProductionLotModel).count()==1
        assert db.query(QualityProductionDefect).count()==1
        assert db.get(ProductionWorkOrder,order).production_qty==50
