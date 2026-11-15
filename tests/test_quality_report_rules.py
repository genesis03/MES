import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from test_quality_performance import quality,seed,external_delivery,item
from test_production_sync import setup
from core.security import get_current_user
from models.sales import SalesOrderMaster,SalesOrderItem,ShipmentMaster,ShipmentItem
from models.partner import Partner
from models.quality_performance import QualityPerformanceMonth
from models.subcontract_fault_sync import ExternalSubcontractFault
from routers import quality_performance as api
from services.quality_performance_service import blank_manual,group_material
from services.quality_performance_excel import TEMPLATE


def native_delivery(env,qty=250,status='CONFIRMED',key='NATIVE'):
    with env.sessions() as db:
        product=item(db,key+'-PART','납품품목')
        partner=Partner(partner_code=key,partner_name='고객사',partner_type='CUSTOMER');db.add(partner);db.flush()
        order=SalesOrderMaster(order_no=key,order_date='2026-10-01',customer_id=partner.id,customer_name=partner.partner_name);db.add(order);db.flush()
        line=SalesOrderItem(order_id=order.id,item_id=product.id,part_no=product.part_no,order_qty=qty);db.add(line);db.flush()
        shipment=ShipmentMaster(shipment_no=key,shipment_date='2026-10-15',sales_order_id=order.id,customer_id=partner.id,customer_name=partner.partner_name,status=status);db.add(shipment);db.flush()
        db.add(ShipmentItem(shipment_id=shipment.id,sales_order_item_id=line.id,item_id=product.id,part_no=product.part_no,shipped_qty=qty));db.commit()


@pytest.mark.parametrize('part',['280089','280098','280144','YR280554','YR280555','yr280554'])
def test_confirmed_cap_parts_are_classified_independently(part):
    assert group_material(part,'동소재')=='CAP'


def test_external_delivery_uses_detail_qty_not_original_or_allocated_lots(quality):
    external_delivery(quality,key='MULTI',items=[{'RULE_NO':'D1','TR_QTY':'300','lots':[{'LOT_QTY':'9000','JOB_QTY':'100'},{'LOT_QTY':'9000','JOB_QTY':'200'}]},{'RULE_NO':'D2','TR_QTY':'200','lots':[]}])
    external_delivery(quality,key='CANCEL',status='OUT_CANCEL',quantity=2000)
    external_delivery(quality,key='DRAFT',status='OUT_REG',quantity=3000)
    external_delivery(quality,key='ABSENT',present=False,quantity=4000)
    external_delivery(quality,key='LATER',month='2026-11',quantity=5000)
    native_delivery(quality,qty=600)
    data=quality.client.get(api.API+'?month=2026-10').json()
    assert data['months'][9]['delivery_qty']==500
    assert data['months'][8]['delivery_qty']==0
    form=quality.client.get(api.API+'/form?month=2026-10').json()
    assert form['delivery_qty']==500 and form['cells']['5']['M19']['text']=='500'
    workbook=load_workbook(BytesIO(quality.client.get(api.API+'/excel?month=2026-10').content),data_only=True)
    assert workbook['5']['M19'].value==500


def test_native_delivery_excludes_cancelled_and_external_copies(quality):
    external_delivery(quality,quantity=9999)
    native_delivery(quality,qty=250)
    native_delivery(quality,qty=888,status='CANCELLED',key='CANCELLED')
    data=quality.client.get(api.API+'?month=2026-10&source=MES').json()
    assert data['months'][9]['delivery_qty']==250


def test_legacy_manual_delivery_cannot_override_auto_delivery(quality):
    external_delivery(quality,quantity=2000)
    manual=blank_manual();manual.update(delivery_qty=999999,customer_bad=2,customer_cases=1)
    assert quality.client.put(api.API,json={'month':'2026-10','version':0,'manual':manual}).status_code==200
    result=quality.client.get(api.API+'/form?month=2026-10').json()
    assert result['version']==1 and result['manual']['customer_bad']==2
    assert result['delivery_qty']==2000
    assert result['cells']['5']['M19']['text']=='2,000'
    assert result['cells']['5']['M20']['text']=='2'
    assert result['cells']['5']['M21']['text']=='1'
    assert result['cells']['5']['M22']['text']=='1,000PPM'
    assert quality.client.get(api.API+'?month=2026-10').json()['overview'][9]['customer_ppm']==1000
    september=quality.client.get(api.API+'/form?month=2026-09').json()
    assert september['manual']['customer_bad'] is None and september['manual']['customer_cases'] is None


def test_only_stain_and_peeling_are_reported_without_changing_source(quality):
    seed(quality)
    report=quality.client.get(api.API+'?month=2026-10').json()
    assert report['months'][9]['subcontract']['혜성금속']=={'qty':1000,'bad':5}
    assert report['months'][9]['subcontract_types']=={'얼룩':2,'박리':3}
    book=load_workbook(BytesIO(quality.client.get(api.API+'/excel?month=2026-10').content),data_only=True)
    assert [book['3-2'][f'K{row}'].value for row in range(20,26)]==[0,0,3,2,0,0]
    with quality.sessions() as db:
        assert db.query(ExternalSubcontractFault).one().defect_qty=='9'
        assert json.loads(db.query(ExternalSubcontractFault).one().raw_json)['F1']=='4'


def test_unmapped_other_type_does_not_hide_known_stain_or_peeling(quality):
    seed(quality)
    with quality.sessions() as db:
        row=db.query(ExternalSubcontractFault).one();raw=json.loads(row.raw_json);raw['F10']='7';row.raw_json=json.dumps(raw);db.commit()
    report=quality.client.get(api.API+'?month=2026-10').json()
    assert report['months'][9]['subcontract_types']=={'얼룩':2,'박리':3}
    assert report['months'][9]['subcontract']['혜성금속']['bad']==5


def test_native_subcontract_uses_receipts_and_selected_types(quality,monkeypatch):
    seed(quality)
    from services import quality_performance_service as service
    rows=[dict(record_source='MES',date='2026-10-10',partner_name='메인텍',part_no='P',inbound_qty=800,defect_qty=25,by_type={'1':15,'9':4,'11':6})]
    monkeypatch.setattr(service,'subcontract_defect_status',lambda *args,**kwargs:{'items':rows})
    result=quality.client.get(api.API+'?month=2026-10&source=MES').json()
    assert result['months'][9]['subcontract']['메인텍']=={'qty':800,'bad':10}
    assert result['months'][9]['subcontract']['혜성금속']=={'qty':0,'bad':0}
    assert result['months'][9]['subcontract_types']=={'얼룩':4,'박리':6}


def test_invalid_or_duplicate_shipment_detail_is_flagged_not_partly_counted(quality):
    external_delivery(quality,key='DUP',items=[{'RULE_NO':'I','TR_QTY':'100'},{'RULE_NO':'I','TR_QTY':'200'}])
    external_delivery(quality,key='BAD',items=[{'RULE_NO':'X','TR_QTY':'NaN'}])
    result=quality.client.get(api.API+'?month=2026-10').json()
    assert result['months'][9]['delivery_qty']==0
    assert sum('납품수량 미확인 전표 제외' in s for s in result['issues'])==2


def test_read_only_account_has_no_customer_save_and_api_still_blocks_write(quality):
    quality.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{api.MENU:'READ'}}))
    page=quality.client.get(api.MENU)
    assert page.status_code==200 and 'data-can-write="false"' in page.text
    assert 'id="qualityCustomerSave"' not in page.text
    assert quality.client.put(api.API,json={'month':'2026-10','version':0,'manual':blank_manual()}).status_code==403
    with quality.sessions() as db:assert db.query(QualityPerformanceMonth).count()==0


def test_2024_2025_comparison_quantities_remain_template_values(quality):
    seed(quality)
    reference=load_workbook(TEMPLATE,data_only=True,read_only=True)
    output=load_workbook(BytesIO(quality.client.get(api.API+'/excel?month=2026-10').content),data_only=True,read_only=True)
    for sheet in ('1','2','3','4','5'):
        for column in ('B','C'):
            for row in (19,20):assert output[sheet][f'{column}{row}'].value==reference[sheet][f'{column}{row}'].value
    assert output['5']['B21'].value==reference['5']['B21'].value
    assert output['5']['C21'].value==reference['5']['C21'].value
    reference.close();output.close()
