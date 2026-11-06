import json
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from lxml import etree as ET
import pytest
from openpyxl import load_workbook
from test_production_sync import setup
from core.security import get_current_user
from models.models import ItemMasterModel,ItemBomModel,ProcessModel,PurchaseInboundMaster
from models.production_sync import ExternalProductionRecord
from models.inbound_sync import ExternalInboundRecord
from models.subcontract_fault_sync import ExternalSubcontractFault
from models.quality_performance import QualityPerformanceMonth,QualityPerformanceAudit
from routers import quality_performance as api
from services import production_sync_service as sync
from services.quality_performance_service import build_report,blank_manual,group_material
from services.quality_performance_excel import build_excel,TEMPLATE,S,NS


@pytest.fixture
def quality(setup,monkeypatch):
    setup.app.include_router(api.router);monkeypatch.setattr(api,'today',lambda:date(2026,11,6))
    return setup


def item(db,part,name,**changes):
    row=ItemMasterModel(part_no=part,part_name=name,account_type='PROD',material_type='SEMI',created_at='2026-01-01',**changes);db.add(row);db.flush();return row


def seed(env):
    with env.sessions() as db:
        parent=item(db,'310061','ROUND TERMINAL');body=item(db,'310062-C','ROUND TERMINAL ASSEMBLY')
        db.add(ItemBomModel(parent_item_id=parent.id,child_item_id=body.id,parent_part_no=parent.part_no,child_part_no=body.part_no,quantity=1,created_at='2026-01-01'))
        item(db,'310194-C','SV1 COMBO LEFT');item(db,'310201-C','AX1-EV INLET')
        db.add_all([ProcessModel(process_code=code,process_name=name,created_at='2026-01-01') for code,name in [('LATHE','복합선반'),('TAP','탭핑'),('ASSEMBLY','조립')]])
        now=sync.now()
        for i,(part,material,qty) in enumerate([('M-RAW','동소재','100'),('280098','단자캡','200'),('YR280554','','300'),('480187','단자캡','400'),('480207','','500')]):
            db.add(ExternalInboundRecord(source_key='IN'+str(i),inbound_no='IN'+str(i),history_no='H'+str(i),part_no=part,inbound_date='2026-10-03',confirm_date='2026-10-04',partner_name='성재금속',confirmed_qty=qty,product_type='원자재',material_type=material,raw_json='{}',content_hash=str(i),first_seen_at=now,last_seen_at=now,changed_at=now))
        for i,(part,process,good,bad,setupqty) in enumerate([('LATHE-A','복합선반',95,2,3),('TAP-B','탭핑',190,4,6),('310062-C','조립',285,6,9),('310194-C','조립',380,8,12),('310201-C','조립',475,10,15)]):
            raw={'F10':str(setupqty),'DEFECTS':[{'code':'F1','name':'찍힘','qty':bad}]}
            db.add(ExternalProductionRecord(source_key='P'+str(i),lot_no='QPF-'+str(i),job_no='JOB'+str(i),part_no=part,process_name=process,work_date='2026-10-05',job_qty=str(good),lot_qty=str(good+bad+setupqty),fault_qty=str(bad),raw_json=json.dumps(raw),content_hash=str(i),first_seen_at=now,last_seen_at=now,changed_at=now))
        raw={'ORI_LOT_QTY':'1000','REST_QTY':'5','FAULT_QTY':'5','F1':'2','F9':'3','APPLY_COMP_NM':'혜성금속','ORDER_COMP_NM':''}
        db.add(ExternalSubcontractFault(source_key='S',confirm_date='2026-10-07',inbound_no='SUB',part_no='PART-Ag',lot_no='SLOT',process_name='은도금',defect_qty='5',raw_json=json.dumps(raw),content_hash='S',first_seen_at=now,last_seen_at=now,changed_at=now))
        db.commit()


def test_groups_goals_and_assembly_are_counted_once(quality):
    seed(quality)
    response=quality.client.get(api.API+'?month=2026-10')
    assert response.status_code==200,response.text
    data=response.json();m=data['months'][9]
    assert data['goals']=={'inbound':.003,'process':.003,'subcontract':.007,'final':.008,'customer':10,'claim':.01}
    assert m['raw']=={'COPPER':100,'CAP':500,'SEAL':900}
    assert m['process']['LATHE']=={'qty':100,'bad':2}
    assert m['process']['TAP']=={'qty':200,'bad':4}
    assert m['final']['BASE']=={'qty':300,'bad':6}
    assert m['final']['SV1']=={'qty':400,'bad':8}
    assert m['final']['AX1-EV']=={'qty':500,'bad':10}
    assert sum(m['process_types'].values())==6
    assert sum(m['final_types'].values())==24
    assert m['subcontract']['혜성금속']=={'qty':1000,'bad':5}
    assert sum(m['subcontract_types'].values())==5
    with quality.sessions() as db:assert db.query(PurchaseInboundMaster).count()==0


def test_manual_zero_blank_conflict_audit_and_permissions(quality):
    manual=blank_manual();manual.update(delivery_qty=10000,customer_bad=1,customer_cases=1,revenue=100000,author='작성자')
    for group in manual['claims'].values():group.update(occurred=0,agreed=0,deducted=0)
    manual['claims']['SV1']['occurred']=1000
    payload={'month':'2026-10','version':0,'manual':manual}
    saved=quality.client.put(api.API,json=payload);assert saved.status_code==200,saved.text
    assert saved.json()['version']==1
    assert quality.client.put(api.API,json=payload).status_code==409
    report=quality.client.get(api.API+'?month=2026-10').json()
    assert report['overview'][9]['customer_ppm']==100
    assert report['overview'][9]['claim_rate']==.01
    assert report['manual']['claims']['BASE']['occurred']==0
    manual['customer_bad']=None
    assert quality.client.put(api.API,json=payload|{'version':1}).status_code==200
    assert quality.client.get(api.API+'?month=2026-10').json()['overview'][9]['customer_ppm'] is None
    history=quality.client.get(api.API+'/history?month=2026-10').json()
    assert len(history)==2 and history[0]['before']['customer_bad']==1 and history[0]['after']['customer_bad'] is None
    quality.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions=json.dumps({'menu_access':{api.MENU:'READ'}}))
    assert quality.client.get(api.API+'?month=2026-10').status_code==200
    assert quality.client.put(api.API,json=payload).status_code==403
    quality.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='none',role='USER',permissions='{}')
    assert quality.client.get(api.API+'/excel?month=2026-10').status_code==403


def test_template_format_drawings_charts_and_formulas_are_preserved(quality):
    seed(quality)
    response=quality.client.get(api.API+'/excel?month=2026-10')
    assert response.status_code==200,response.text
    original=zipfile.ZipFile(TEMPLATE);out=zipfile.ZipFile(BytesIO(response.content))
    removed={'xl/charts/chart28.xml','xl/drawings/_rels/drawing17.xml.rels','xl/drawings/drawing17.xml',
             'xl/printerSettings/printerSettings17.bin','xl/worksheets/_rels/sheet17.xml.rels','xl/worksheets/sheet17.xml'}
    assert set(original.namelist())-set(out.namelist())==removed
    assert not set(out.namelist())-set(original.namelist())
    for path in original.namelist():
        if path in removed or path=='xl/_rels/workbook.xml.rels':continue
        if path=='xl/drawings/drawing1.xml':
            a=ET.fromstring(original.read(path));b=ET.fromstring(out.read(path));ns={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
            assert b.xpath('.//a:t/text()',namespaces=ns)==['2026','년 ','10','월 품질',' ','실적 보고']
            for root in (a,b):
                for node in root.xpath('.//a:t',namespaces=ns):node.text=''
            assert ET.tostring(a,method='c14n')==ET.tostring(b,method='c14n')
            continue
        if path.startswith(('xl/media/','xl/drawings/','xl/printerSettings/','xl/theme/')) or path=='xl/styles.xml' or path.endswith('.rels'):
            assert original.read(path)==out.read(path),path
    sheets=load_workbook(BytesIO(response.content),data_only=True)
    reference=load_workbook(TEMPLATE,data_only=True)
    assert sheets.sheetnames==[name for name in reference.sheetnames if name!='6'] and len(sheets.sheetnames)==16
    for a,b in zip(sheets,[s for s in reference if s.title!='6']):
        assert set(str(r) for r in a.merged_cells.ranges)==set(str(r) for r in b.merged_cells.ranges)
        assert a.print_area==b.print_area and a.page_setup==b.page_setup
        for row in b:
            for c in row:assert a[c.coordinate].style_id==c.style_id,(a.title,c.coordinate)
    assert sheets['1-1']['L16'].value==100 and sheets['1-1']['L20'].value==500 and sheets['1-1']['L24'].value==900
    assert sheets['2-1']['L26'].value==1200 and sheets['2-1']['L27'].value==24
    assert sheets['2-1']['L31'].value==300 and sheets['2-1']['L32'].value==6
    assert sheets['4-1']['L29'].value==24 and sheets['4-2']['K19'].value==24
    assert sheets['요약']['F11'].value==pytest.approx(24/1200)
    assert sheets['5']['M22'].value is None
    formulas=load_workbook(BytesIO(response.content),data_only=False)
    assert formulas['요약']['F13'].value is None
    assert '6' not in formulas.sheetnames
    assert not any(c.data_type=='f' and "'6'!" in c.value for sheet in formulas for row in sheet for c in row)
    assert '22' in formulas['5']['P10'].value
    assert formulas['3-2']['R23'].value=='=K23'
    assert not any(c.data_type=='e' for s in sheets for row in s for c in row)
    chartpaths=[p for p in out.namelist() if p.startswith('xl/charts/chart') and p.endswith('.xml')]
    assert len(chartpaths)==27


@pytest.mark.parametrize('month',['bad','2026-13','2026-11','2027-01'])
def test_bad_or_not_yet_closed_month_rejected(quality,month):
    assert quality.client.get(api.API,params={'month':month}).status_code==422


def test_shared_assembly_types_do_not_repeat_for_each_lot(quality):
    seed(quality)
    with quality.sessions() as db:
        now=sync.now()
        for i in range(2):
            raw={'F10':'0','DEFECTS':[],'ASSEMBLY_GROUP_LOTS':['SHARED-1','SHARED-2'],
                'ASSEMBLY_GROUP_DEFECTS':[{'code':'F1','name':'찍힘','qty':2}]}
            db.add(ExternalProductionRecord(source_key='SHARED'+str(i),lot_no='SHARED-'+str(i+1),job_no='SJ'+str(i),part_no='310194-C',process_name='조립',work_date='2026-10-05',job_qty='49',lot_qty='50',fault_qty='1',raw_json=json.dumps(raw),content_hash='SH'+str(i),first_seen_at=now,last_seen_at=now,changed_at=now))
        db.commit()
    report=quality.client.get(api.API+'?month=2026-10').json()
    assert report['months'][9]['final']['SV1']=={'qty':500,'bad':10}
    assert sum(report['months'][9]['final_types'].values())==26


def test_manual_month_isolation_and_excel_cached_ppm_claim_rate(quality):
    seed(quality)
    for month,bad in [('2026-09',0),('2026-10',2)]:
        manual=blank_manual();manual.update(delivery_qty=1000,customer_bad=bad,customer_cases=1,revenue=10000,author='=원문 작성자')
        for group in manual['claims'].values():group.update(occurred=0,agreed=0,deducted=0)
        manual['claims']['BASE']['occurred']=100
        r=quality.client.put(api.API,json={'month':month,'version':0,'manual':manual});assert r.status_code==200,r.text
    response=quality.client.get(api.API+'/excel?month=2026-10');assert response.status_code==200,response.text
    w=load_workbook(BytesIO(response.content),data_only=True)
    assert w['5']['M22'].value==2000 and w['5']['P10'].value==2000
    assert w['요약']['F12'].value==2000
    assert '6' not in w.sheetnames and w['요약']['F13'].value==.01
    assert w['요약']['G13'].value is None  # Jan-Aug missing, never partial annual result.
    assert w['5']['P19'].value is None
    z=zipfile.ZipFile(BytesIO(response.content));root=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    author=root.find('.//s:c[@r="AT30"]',NS)
    assert author.get('t')=='inlineStr' and author.find('s:f',NS) is None
    with quality.sessions() as db:
        assert db.query(QualityPerformanceMonth).count()==2 and db.query(QualityPerformanceAudit).count()==2


def test_source_selection_does_not_mix_external_into_native_report(quality):
    seed(quality)
    data=quality.client.get(api.API+'?month=2026-10&source=MES').json()
    assert data['overview'][9]['process_qty']==0 and data['overview'][9]['final_qty']==0
    assert sum(data['months'][9]['raw'].values())==0
    assert data['overview'][9]['subcontract_qty']==0


def test_january_has_prior_year_comparison_without_index_zero_and_input_validation(quality):
    r=quality.client.get(api.API+'/excel?month=2026-01');assert r.status_code==200,r.text
    w=load_workbook(BytesIO(r.content),data_only=False)
    assert 'C21' in w['2']['P8'].value
    manual=blank_manual();manual['revenue']=-1
    assert quality.client.put(api.API,json={'month':'2026-10','version':0,'manual':manual}).status_code==422
    manual=blank_manual();manual['claims']={'BAD':{}}
    assert quality.client.put(api.API,json={'month':'2026-10','version':0,'manual':manual}).status_code==422


def test_native_source_collects_native_receipts_and_assembly_without_external_copies(quality):
    from models.models import PurchaseInboundItem,WarehouseMasterModel,StorageLocationModel
    from models.production import ProductionWorkOrder,ProductionPerformance
    seed(quality)
    with quality.sessions() as db:
        raw=ItemMasterModel(part_no='YR280554',part_name='CAP',account_type='RM',material_type='RAW',unit='EA',created_at='2026-01-01');db.add(raw)
        turning=item(db,'NATIVE-A','가공 소재')
        assembly=db.query(ItemMasterModel).filter_by(part_no='310194-C').one()
        db.add(WarehouseMasterModel(warehouse_code='W',warehouse_name='창고',created_at='2026-01-01'))
        db.add(StorageLocationModel(location_code='L',location_name='위치',created_at='2026-01-01'))
        receipt=PurchaseInboundMaster(inbound_no='NATIVE-IN',inbound_date='2026-10-02',partner_name='성민전자',status='CONFIRMED');db.add(receipt);db.flush()
        db.add(PurchaseInboundItem(inbound_id=receipt.id,item_id=raw.id,part_no=raw.part_no,unit='EA',inbound_qty=123,supplier_lot_no='SL',storage_location='L',warehouse_code='W',unit_price=0))
        for number,product,process,good,bad,setupqty in [(1,turning,'LATHE',20,1,2),(2,assembly,'ASSEMBLY',50,2,3)]:
            order=ProductionWorkOrder(work_order_no='NATIVE-WO'+str(number),order_date='2026-10-01',item_id=product.id,part_no=product.part_no,order_qty=100);db.add(order);db.flush()
            db.add(ProductionPerformance(work_order_id=order.id,performance_type='ASSEMBLY' if number==2 else 'MACHINING',performance_date='2026-10-03',process_code=process,good_qty=good,defect_qty=bad,setup_qty=setupqty))
        db.commit()
        before=(db.query(PurchaseInboundMaster).count(),db.query(ProductionPerformance).count(),db.query(ExternalProductionRecord).count())
    report=quality.client.get(api.API+'?month=2026-10&source=MES').json()
    assert report['months'][9]['raw']['CAP']==123
    assert report['overview'][9]['process_qty']==23 and report['overview'][9]['process_bad']==1
    assert report['overview'][9]['final_qty']==55 and report['overview'][9]['final_bad']==2
    assert report['months'][9]['assembly']=={'qty':55,'bad':2}
    with quality.sessions() as db:assert (db.query(PurchaseInboundMaster).count(),db.query(ProductionPerformance).count(),db.query(ExternalProductionRecord).count())==before


def test_type_mismatch_is_visible_without_inflating_report_defect_total(quality):
    seed(quality)
    with quality.sessions() as db:
        record=db.query(ExternalProductionRecord).filter_by(part_no='310194-C').one()
        raw=json.loads(record.raw_json);raw['DEFECTS'][0]['qty']=100
        record.raw_json=json.dumps(raw);db.commit()
    report=quality.client.get(api.API+'?month=2026-10').json()
    assert sum(report['months'][9]['final_types'].values())==24
    assert any('유형 합계' in issue for issue in report['issues'])


def test_downloaded_excel_manual_cells_keep_live_annual_formulas(quality):
    from services.quality_performance_excel import Workbook
    response=quality.client.get(api.API+'/excel?month=2026-01')
    assert response.status_code==200,response.text
    book=Workbook(BytesIO(response.content))
    for coord,value in [('D19',1000),('D20',1),('D21',1)]:book.put('5',coord,value)
    result=load_workbook(book.finish(),data_only=True)
    assert result['5']['P19'].value==1000 and result['5']['P22'].value==1000
    assert '6' not in result.sheetnames
    assert '품질 클레임 비용' not in [str(c.value) for row in result['목차'] for c in row]


def test_removed_sheet_has_no_dangling_package_or_calculation_references(quality):
    import posixpath
    response=quality.client.get(api.API+'/excel?month=2026-10')
    package=zipfile.ZipFile(BytesIO(response.content))
    names=set(package.namelist())
    for path in names:
        if not path.endswith('.rels'):continue
        owner='' if path=='_rels/.rels' else '/'.join(path.rsplit('/_rels/',1))[:-5]
        for relation in ET.fromstring(package.read(path)):
            if relation.get('TargetMode')=='External':continue
            target=relation.get('Target')
            resolved=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(owner),target))
            assert resolved in names,(path,resolved)
    book=ET.fromstring(package.read('xl/workbook.xml'))
    rels={r.get('Id'):r.get('Target') for r in ET.fromstring(package.read('xl/_rels/workbook.xml.rels'))}
    sheet_parts={s.get('sheetId'):'xl/'+rels[s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')] for s in book.find('s:sheets',NS)}
    formula_cells={sid:{c.get('r') for c in ET.fromstring(package.read(part)).xpath('.//s:c[s:f]',namespaces=NS)} for sid,part in sheet_parts.items()}
    for entry in ET.fromstring(package.read('xl/calcChain.xml')):
        assert entry.get('r') in formula_cells[entry.get('i')]
    properties=ET.fromstring(package.read('docProps/app.xml'))
    ns={'ep':'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties','vt':'http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes'}
    assert properties.xpath('ep:HeadingPairs/vt:vector/vt:variant/vt:i4/text()',namespaces=ns)==['16','16']
