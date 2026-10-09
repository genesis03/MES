import io
from openpyxl import load_workbook
from test_production_sync import setup,DAY,row
from routers.daily_job_report import router,Supplement
from services import production_sync_service as sync
from services.daily_job_report import build_workbook,workbook_html
from models.daily_job_report import DailyJobReportSupplement
from models.production_sync import ExternalProductionRecord
from models.production_lot import ProductionLotModel


def test_external_supplement_round_trip_and_readonly_export(setup):
    setup.app.include_router(router)
    token,_=sync.claim(DAY,DAY,'MANUAL');sync.save_day([row()],token)
    result=setup.client.get('/api/production/daily-job-report',params={'day':DAY.isoformat()})
    assert result.status_code==200,result.text
    source=result.json()['items'][0];key=source['key']
    supplement={'raw_input':5,'raw_remaining':2,'cut_bars':1,'raw_setup':7,'operator_name':'Operator','shift':'야간',
                'measurements':[{'label':'Length','spec':'10±0.1','values':[9.99,10,10.01]}]}
    url='/api/production/daily-job-report/EXTERNAL/'+str(source['source_record_id'])
    assert setup.client.put(url,params={'day':DAY.isoformat()},json=supplement).status_code==200
    persisted=setup.client.get('/api/production/daily-job-report',params={'day':DAY.isoformat()}).json()['items'][0]
    assert persisted['supplement']['measurements'][0]['values']==[9.99,10,10.01]
    payload={'day':DAY.isoformat(),'keys':[key]}
    response=setup.client.post('/api/production/daily-job-report/excel',json=payload)
    assert response.status_code==200,response.text
    sheet=load_workbook(io.BytesIO(response.content)).active
    assert [sheet[x].value for x in ['D16','F16','G16']]==[9.99,10,10.01]
    assert sheet['Y12'].value=='5 / 2' and sheet['AC12'].value==1 and sheet['AF12'].value==7
    assert sheet['C3'].value=='야간' and sheet['O7'].value==331 and sheet['Q7'].value==115
    assert sheet['AA15'].value is None
    html=setup.client.post('/api/production/daily-job-report/print',json=payload)
    assert html.status_code==200 and '측정 3' in html.text
    with setup.sessions() as db:
        assert db.query(DailyJobReportSupplement).count()==1
        assert db.query(ExternalProductionRecord).one().job_qty=='216'
        assert db.query(ProductionLotModel).count()==0
    assert setup.client.put(url,params={'day':DAY.isoformat()},json={'measurements':[{'values':[1]}]}).status_code==422
    assert setup.client.put(url,params={'day':'2026-01-01'},json=supplement).status_code==409


def report_row(lot,measurements=0):
    extra=Supplement(measurements=[{'label':str(i),'values':[i,i+.1,i+.2]} for i in range(measurements)]).model_dump()
    return dict(performance_date='2026-10-09',process_name='복합선반',equipment_name='1호기',shift_name='주간',
                operator_name='Worker',part_no='=1+1',part_name='Test',total_qty=100,setup_qty=2,defect_qty=1,
                work_time='20261009090000 ~ 20261009180000',material_lots='RAW: 1 KG',output_lot_no=lot,supplement=extra)


def test_multiple_work_rows_and_inspection_items_are_not_truncated():
    workbook=build_workbook([report_row('LOT1',10),report_row('LOT2'),report_row('LOT3')])
    assert len(workbook.worksheets)==3
    assert workbook.worksheets[0]['AH7'].value=='LOT1' and workbook.worksheets[0]['AH8'].value=='LOT2'
    assert workbook.worksheets[1]['D16'].value==8
    assert workbook.worksheets[2]['AH7'].value=='LOT3'
    assert workbook.worksheets[0]['G7'].data_type=='s'
    assert workbook.worksheets[0]['M7'].value=='09:00 ~ 18:00'
    assert 'LOT3' in workbook_html(workbook) and '검사 계속 2/2' in workbook_html(workbook)


def test_native_quantities_and_current_inspection_standard_match(setup):
    from test_production_sync import add_item
    from models.models import ProcessModel
    from models.production import ProductionWorkOrder,ProductionPerformance
    from models.inspection_standard import InspectionStandard,InspectionStandardItem
    setup.app.include_router(router)
    with setup.sessions() as db:
        item=add_item(db)
        db.add(ProcessModel(process_code='LT',process_name='복합선반',created_at='2026-01-01'))
        db.flush()
        order=ProductionWorkOrder(work_order_no='W1',order_date=DAY.isoformat(),item_id=item,part_no='LOCAL-B',order_qty=500)
        db.add(order);db.flush()
        db.add(ProductionPerformance(work_order_id=order.id,performance_date=DAY.isoformat(),process_code='LT',shift_type='DAY',equipment_name='복합선반 1호기',operator_name='Worker',good_qty=216,defect_qty=0,setup_qty=115))
        standard=InspectionStandard(document_type='PROCESS',item_id=item,inspection_process_code='LT',revision='Rev.00',status='CURRENT',part_no_snapshot='LOCAL-B',part_name_snapshot='Test')
        standard.items.append(InspectionStandardItem(inspection_item_name='외경',spec_text='Ø10±0.1'))
        db.add(standard);db.commit()
    data=setup.client.get('/api/production/daily-job-report',params={'day':DAY.isoformat()}).json()['items'][0]
    assert data['total_qty']==331 and data['setup_qty']==115 and data['operator_name']=='Worker'
    assert data['supplement']['measurements'][0]['label']=='외경'
    assert data['supplement']['measurements'][0]['values']==[None,None,None]


def test_print_preserves_original_fonts_fills_borders_and_blank_spacers():
    workbook=build_workbook([report_row('LOT1')])
    output=workbook_html(workbook)
    assert 'font-size:18.0pt' in output
    assert 'font-weight:700' in output and 'background:#E0E0E0' in output
    assert 'background:#FFFF00' in output
    assert 'border-left:1pt solid' in output
    assert 'font-size:0pt' in output
    assert workbook.active['D7'].alignment.horizontal=='left'
    assert workbook.active['F16'].fill.fgColor.rgb=='FFFFFF00'
    assert workbook.active['G14'].font.sz==7
