import json
import pytest
from test_production_sync import setup, DAY, add_item, row as production_row
from test_packing_sync import row as packing_row
from test_subcontract_fault_sync import row as fault_row
from routers.packing_sync import router as packing_sync_router
from routers.packing import router as packing_router
from routers.subcontract_fault_sync import router as fault_router
from routers.quality_defect_status import router as quality_router
from models.models import ItemMasterModel
from services import production_sync_service as sync
from services.external_item_display import item_display


@pytest.mark.parametrize('kind,source_process,part,source_row,endpoint',[
    ('production','복합선반','LOCAL-A',production_row,'/api/production/external-sync/records'),
    ('packing','포장','LOCAL-FINISHED',packing_row,'/api/packing/external-sync/records'),
    ('subcontract','은도금','LOCAL-Ag',fault_row,'/api/subcontract/fault-sync/records'),
])
def test_names_follow_master_and_preserve_source_without_reimport(setup,kind,source_process,part,source_row,endpoint):
    setup.app.include_router(packing_sync_router);setup.app.include_router(packing_router);setup.app.include_router(fault_router);setup.app.include_router(quality_router)
    source=source_row(ITEM_NUM='UNLINKED-SOURCE',PRODUCT_NM='외부 원본 품명')
    if kind!='packing':source['PROC_TYPE_NM']=source_process
    token,_=sync.claim(DAY,DAY,'MANUAL',kind)
    sync.save_day([source],token,kind,queried_day=DAY)
    initial=setup.client.get(endpoint).json()['rows'][0]
    assert initial['part_name']=='외부 원본 품명' and initial['name_source']=='EXTERNAL' and not initial['linked']
    with setup.sessions() as db:
        item_id=add_item(db,part=part)
        item=db.get(ItemMasterModel,item_id);item.part_name='외부 원본 품명'
        if kind=='packing':item.material_type='FINISHED';item.account_type='PROD'
        db.commit()
    # Even an identical name never connects a different external part by name.
    assert setup.client.get(endpoint).json()['rows'][0]['linked'] is False
    maps=setup.client.get('/api/production/external-sync/item-maps').json()
    assert any(r['source_part_no']=='UNLINKED-SOURCE' and r['source_process']==source_process for r in maps)
    result=setup.client.put('/api/production/external-sync/item-maps',json={
        'source_part_no':'UNLINKED-SOURCE','source_process':source_process,'item_id':item_id})
    assert result.status_code==200,result.text
    for name in ('내부 표준 품명','변경된 내부 품명',''):
        with setup.sessions() as db:
            db.get(ItemMasterModel,item_id).part_name=name;db.commit()
        linked=setup.client.get(endpoint).json()['rows'][0]
        assert linked['part_name']==name and linked['name_source']=='MES' and linked['linked']
        assert linked['source_part_name']=='외부 원본 품명'
        url={'production':'/api/production/performance-status','packing':'/api/packing/status',
             'subcontract':'/api/quality/defect-status/subcontract'}[kind]
        public=setup.client.get(url).json()
        public_row=public[0] if kind=='production' else public['items'][0]
        assert public_row['part_name']==name and public_row['source_part_name']=='외부 원본 품명'
    with setup.sessions() as db:
        Record=sync.backend(kind)[2]
        assert json.loads(db.query(Record).one().raw_json)['PRODUCT_NM']=='외부 원본 품명'
        db.get(ItemMasterModel,item_id).is_active='N';db.commit()
    inactive=setup.client.get(endpoint).json()['rows'][0]
    assert not inactive['linked'] and inactive['part_name']=='외부 원본 품명'


def test_group_name_is_not_assigned_to_an_unidentified_part():
    assert item_display(None,{'UNASSIGNED_PRODUCT_NAME':'다른 행일 수 있는 품명'})['part_name']==''
