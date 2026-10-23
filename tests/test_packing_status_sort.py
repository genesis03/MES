import pytest
from test_production_sync import setup, DAY, add_item
from test_packing_sync import enable_routes, row
from models.packing import PackingMaster, PackingBox
from services import production_sync_service as sync


def seed(setup):
    enable_routes(setup)
    with setup.sessions() as db:
        item_id=add_item(db,part='SORT-FINISHED')
        for index,(part,qty,status) in enumerate([('다',2,'PACKED'),('가',100,'PACKED'),
                                                ('나',10,'PACKED'),('취소',1,'CANCELLED')],start=1):
            master=PackingMaster(packing_no=f'SORT-{index}',packing_date=DAY.isoformat(),
                item_id=item_id,part_no=part,part_name=part+' 품명',box_count=1,box_qty=qty,total_qty=qty,status=status)
            db.add(master);db.flush()
            db.add(PackingBox(packing_id=master.id,box_no=1,package_lot_no=f'LOCAL-{index}',box_qty=qty))
        db.commit()
    token,_=sync.claim(DAY,DAY,'MANUAL','packing')
    sync.save_day([row(part='EXTERNAL',lot='EXTERNAL-LOT',LOT_QTY='3',JOB_QTY='1',
                       CONFIRM_DATE='20260326',APPLY_COMP_NM='외부 거래처')],token,'packing')


@pytest.mark.parametrize('direction,quantities',[('asc',[2,3,10,100]),('desc',[100,10,3,2])])
def test_numeric_sort_merges_sources_before_limit(setup,direction,quantities):
    seed(setup)
    params={'sort_by':'packing_qty','sort_order':direction}
    data=setup.client.get('/api/packing/status',params=params).json()
    assert [r['packing_qty'] for r in data['items']]==quantities
    assert data['total']==4
    limited=setup.client.get('/api/packing/status',params=params|{'limit':1}).json()
    assert limited['items']==data['items'][:1] and limited['truncated'] and limited['total']==4


def test_text_dates_unknown_stock_and_filters(setup):
    seed(setup)
    url='/api/packing/status'
    data=setup.client.get(url,params={'sort_by':'part_no'}).json()['items']
    assert [r['part_no'] for r in data]==['EXTERNAL','가','나','다']
    for direction in ('asc','desc'):
        date_rows=setup.client.get(url,params={'sort_by':'shipment_date','sort_order':direction}).json()['items']
        assert date_rows[0]['lot_no']=='EXTERNAL-LOT'
        stock=setup.client.get(url,params={'sort_by':'stock_qty','sort_order':direction}).json()['items']
        assert stock[-1]['lot_no']=='EXTERNAL-LOT' and stock[-1]['stock_qty'] is None
        assert all(r['record_source']=='MES' for r in stock[:-1])
    filtered=setup.client.get(url,params={'sort_by':'packing_qty','part_no':'가'}).json()
    assert filtered['total']==1 and filtered['items'][0]['part_no']=='가'
    assert setup.client.get(url,params={'sort_by':'raw_json'}).status_code==422
    assert setup.client.get(url,params={'sort_order':'invalid'}).status_code==422


def test_three_priorities_use_each_direction_and_sort_before_limit(setup):
    seed(setup)
    with setup.sessions() as db:
        item_id=db.query(PackingMaster).first().item_id
        master=PackingMaster(packing_no='TIE',packing_date=DAY.isoformat(),item_id=item_id,
            part_no='하',part_name='동률 확인',box_count=1,box_qty=2,total_qty=2,status='PACKED')
        db.add(master);db.flush()
        db.add(PackingBox(packing_id=master.id,box_no=1,package_lot_no='TIE',box_qty=2));db.commit()
    params=[('sort_by','shipment_qty'),('sort_order','asc'),('sort_by','packing_qty'),('sort_order','asc'),
            ('sort_by','part_no'),('sort_order','desc')]
    data=setup.client.get('/api/packing/status',params=params).json()
    assert [r['part_no'] for r in data['items']]==['하','다','나','가','EXTERNAL']
    limited=setup.client.get('/api/packing/status',params=params+[('limit','2')]).json()
    assert limited['items']==data['items'][:2] and limited['total']==5


def test_ten_unique_criteria_and_invalid_combinations(setup):
    seed(setup)
    fields=['part_no','part_name','lot_no','packing_date','packing_qty','shipment_qty',
            'shipment_date','customer_name','stock_qty','source_label']
    params=[('sort_by',field) for field in fields]
    assert setup.client.get('/api/packing/status',params=params).status_code==200
    assert setup.client.get('/api/packing/status',params=params+[('sort_by','part_no')]).status_code==422
    assert setup.client.get('/api/packing/status',params=[('sort_by','part_no'),('sort_by','part_no')]).status_code==422
    assert setup.client.get('/api/packing/status',params=params+[('sort_order','asc')]).status_code==422
