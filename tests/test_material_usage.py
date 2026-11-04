import json
from io import BytesIO
import pytest
from openpyxl import load_workbook
from test_inventory_ledger import env
from routers.material_usage import router, API, MENU
from models.material_usage import MaterialUsageRecord, MaterialUsageAudit
from models.production_lot import ProductionLotModel
from models.lot_consumption import LotConsumptionModel

@pytest.fixture
def manual(env):
    env.client.app.include_router(router)
    return env

def payload(**changes):
    return dict(request_key='manual-1',work_date='2026-11-04',worker='작업자',part_no='YR310313',production_qty='2000',materials=[dict(part_no='91012',unit='EA',expected_qty='2000',issued_qty='2000',used_qty='2000',scrap_qty='0',remaining_qty='0')])|changes

def save(env,**changes):
    r=env.client.post(API,json=payload(**changes))
    assert r.status_code==200,r.text
    return r.json()

def test_manual_no_stock_and_idempotency(manual):
    r=save(manual)
    assert r['flags']==['정상(기록 기준)']
    assert save(manual)['id']==r['id']
    assert manual.db.query(MaterialUsageRecord).count()==1
    assert manual.db.query(MaterialUsageAudit).count()==1
    assert manual.db.query(ProductionLotModel).count()==0
    assert manual.db.query(LotConsumptionModel).count()==0
    assert manual.client.post(API,json=payload(production_qty='3')).status_code==409

def test_blank_zero_and_missing_material(manual):
    r=save(manual,materials=[dict(part_no='M',expected_qty='1',used_qty='0')])
    assert '사용 0 · 누락 확인' in r['flags']
    assert '수량 미입력' in r['flags']
    assert r['materials'][0]['remaining_qty'] is None
    assert r['materials'][0]['balance_difference'] is None
    assert save(manual,request_key='empty',materials=[])['flags']==['자재 기록 없음']

def test_multiple_lots_grouped_case_insensitively(manual):
    rows=[dict(part_no='Mat',unit='EA',expected_qty='2000',issued_qty='500',used_qty='500',scrap_qty='0',remaining_qty='0'),dict(part_no='mat',unit='ea',issued_qty='1500',used_qty='1500',scrap_qty='0',remaining_qty='0')]
    r=save(manual,materials=rows)
    assert r['flags']==['정상(기록 기준)']
    assert len(r['summary'])==1
    assert r['summary'][0]['used_qty']=='2000'
    rows[1]['expected_qty']='2000'
    assert manual.client.post(API,json=payload(request_key='bad',materials=rows)).status_code==422

def test_workbook_unexplained_difference(manual):
    rows=[dict(part_no='91012',issued_qty=str(i),used_qty=str(u),scrap_qty='0',remaining_qty='0') for i,u in [(500,500),(1000,1000),(1000,980)]]
    r=save(manual,materials=rows)
    assert set(r['flags'])=={'수불 불일치','필요수량 미입력'}
    assert r['materials'][-1]['balance_difference']=='20'

@pytest.mark.parametrize('qty',['-1','NaN','0.0000001'])
def test_invalid_qty(manual,qty):
    assert manual.client.post(API,json=payload(production_qty=qty)).status_code==422

def test_edit_conflict_cancel_audit(manual):
    r=save(manual); data=payload(production_qty='38');data.pop('request_key');data.update(version=1,reason='수량 정정')
    url=API+'/'+str(r['id'])
    updated=manual.client.put(url,json=data)
    assert updated.status_code==200,updated.text
    assert updated.json()['version']==2
    assert manual.client.put(url,json=data).status_code==409
    history=manual.client.get(url+'/history').json()
    assert history[0]['before']['production_qty']=='2000'
    assert history[0]['after']['production_qty']=='38'
    assert manual.client.post(url+'/cancel',json={'version':2,'reason':'오입력'}).status_code==200
    assert manual.client.put(url,json=data|{'version':3}).status_code==409
    assert manual.client.get(API+'?start=2026-11-01&end=2026-11-30').json()['total']==0
    assert manual.client.get(API+'?start=2026-11-01&end=2026-11-30&include_cancelled=true').json()['total']==1

def test_filters_export_and_permissions(manual):
    save(manual,worker='=cmd')
    save(manual,request_key='second',part_no='OTHER',materials=[])
    query='?start=2026-11-01&end=2026-11-30&part_no=310313'
    assert manual.client.get(API+query).json()['total']==1
    assert manual.client.get(API+'?start=2026-11-01&end=2026-11-30&flag=자재 기록 없음&size=1').json()['items'][0]['part_no']=='OTHER'
    book=load_workbook(BytesIO(manual.client.get(API+'/export'+query).content))
    assert book.active['B2'].value=="'=cmd"
    manual.state.user.username='reader';manual.state.user.role='USER';manual.state.user.permissions=json.dumps({'menu_access':{MENU:'READ'}})
    assert manual.client.get(API+query).status_code==200
    assert manual.client.post(API,json=payload(request_key='denied')).status_code==403
    manual.state.user.permissions='{}'
    assert manual.client.get(API+'/export'+query).status_code==403
