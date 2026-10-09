from test_production_sync import setup, add_item, DAY
from models.models import ProcessModel, ItemBomModel
from models.production import ProductionWorkOrder
from models.production_run import ProductionRun
from models.worker import WorkerMaster, WorkerProcess
from models.equipment import EquipmentMaster
from routers.production_run import router as run_router
from routers.production import router as production_router


def prepare(setup):
    setup.app.include_router(run_router)
    setup.app.include_router(production_router)
    with setup.sessions() as db:
        for code,name in [('LT','복합선반'),('TP','탭핑'),('DOT','세레이션'),('ASSY','조립'),('PL','은도금'),('PACK','포장'),('INSP','검사')]:
            db.add(ProcessModel(process_code=code,process_name=name,created_at='2026-01-01'))
        db.flush()
        item=add_item(db)
        material=add_item(db,part='RAW')
        db.add(ItemBomModel(parent_item_id=item,child_item_id=material,parent_part_no='LOCAL-B',child_part_no='RAW',process_code='LT',quantity=1,unit='EA',created_at='2026-01-01'))
        orders=[ProductionWorkOrder(work_order_no=f'W{i}',order_date=DAY.isoformat(),item_id=item,part_no='LOCAL-B',order_qty=100) for i in range(2)]
        db.add_all(orders)
        worker=WorkerMaster(worker_code='WORKER',worker_name='작업자')
        db.add(worker);db.flush()
        db.add(WorkerProcess(worker_id=worker.id,process_code='LT'))
        equipment=[EquipmentMaster(equipment_code=f'LT{i}',equipment_name=f'복합선반 {i}호기',process_code='LT',machine_no=str(i)) for i in (1,2)]
        db.add_all(equipment);db.commit()
        return orders[0].id,orders[1].id,worker.id,equipment[0].id,equipment[1].id


def test_same_item_can_run_on_different_equipment_but_not_same_equipment(setup):
    first,other,worker,machine1,machine2=prepare(setup)
    payload=dict(work_order_id=first,performance_date=DAY.isoformat(),process_code='LT',operator_id=worker,equipment_id=machine1,shift_type='DAY',performance_type='MACHINING')
    response=setup.client.post('/api/production-run/start',json=payload)
    assert response.status_code==200,response.text
    assert setup.client.post('/api/production-run/start',json=payload).status_code==409
    # A different work order for the same item on the same equipment is also a duplicate.
    assert setup.client.post('/api/production-run/start',json=payload|{'work_order_id':other}).status_code==409
    response2=setup.client.post('/api/production-run/start',json=payload|{'equipment_id':machine2})
    assert response2.status_code==200,response2.text
    assert response2.json()['equipment_id']==machine2
    with setup.sessions() as db:
        assert db.query(ProductionRun).filter_by(status='IN_PROGRESS').count()==2
        db.get(ProductionRun,response.json()['id']).status='CANCELLED'
        db.commit()
    assert setup.client.post('/api/production-run/start',json=payload).status_code==200


def test_process_choices_and_registration_enforce_performance_type(setup):
    first,_,worker,machine1,_=prepare(setup)
    endpoint='/api/production/processes'
    assert len(setup.client.get(endpoint).json())==7
    assert {p['process_code'] for p in setup.client.get(endpoint,params={'performance_type':'MACHINING'}).json()}=={'LT','TP','DOT'}
    assert {p['process_code'] for p in setup.client.get(endpoint,params={'performance_type':'ASSEMBLY'}).json()}=={'ASSY'}
    assert setup.client.get(endpoint,params={'performance_type':'WRONG'}).status_code==422
    payload=dict(work_order_id=first,performance_date=DAY.isoformat(),process_code='LT',operator_id=worker,equipment_id=machine1,shift_type='DAY',performance_type='ASSEMBLY')
    for process,kind in [('LT','ASSEMBLY'),('ASSY','MACHINING'),('PL','MACHINING'),('PACK','ASSEMBLY')]:
        response=setup.client.post('/api/production-run/start',json=payload|{'process_code':process,'performance_type':kind})
        assert response.status_code==400,response.text
        assert '공정만 선택' in response.json()['detail']
    with setup.sessions() as db:
        assert db.query(ProductionRun).count()==0
