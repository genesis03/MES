"""The original form, its charts and Excel download share the same report data."""
from datetime import date
from types import SimpleNamespace
from io import BytesIO
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest

from test_production_sync import setup
from test_quality_performance import quality, seed
from core.security import get_current_user
from routers import quality_performance as api
from services.quality_performance_service import blank_manual
from services.quality_performance_form import format_cell, form_definition


def test_automatic_data_populates_original_cells_and_all_charts(quality):
    seed(quality)
    response = quality.client.get(api.API + '/form?month=2026-10')
    assert response.status_code == 200, response.text
    result = response.json()
    cells = result['cells']
    assert cells['1-1']['L16']['text'] == '100'
    assert cells['1-1']['L20']['text'] == '500'
    assert cells['1-1']['L24']['text'] == '900'
    assert cells['2-1']['L16']['text'] == '100'
    assert cells['2-1']['L21']['text'] == '200'
    assert cells['2-1']['L26']['text'] == '1,200'
    assert cells['2-1']['L27']['text'] == '24'
    assert cells['2-1']['L31']['text'] == '300'
    assert cells['2-1']['L32']['text'] == '6'
    assert cells['4-1']['L15']['text'] == '300'
    assert cells['4-1']['L21']['text'] == '400'
    assert cells['4-1']['L24']['text'] == '500'
    assert cells['요약']['F9']['text'] == '2.00%'
    assert cells['요약']['F11']['text'] == '2.00%'
    assert cells['3-1']['M18']['text'] == '1,000'
    assert cells['3-1']['M19']['text'] == '5'
    assert len(result['charts']) == 27
    for name in ('chart1.xml','chart2.xml'):
        for series in result['charts']['xl/charts/'+name]['series']:
            assert len(series['categories']) == len(series['values'])
            assert all(series['categories'])
    assert len(next(s for s in form_definition()['sheets'] if s['name']=='요약')['charts']) == 2
    assert result['drawings']['표지'][0] == '2026년 10월 품질 실적 보고'


def test_manual_unknowns_and_future_months_stay_blank(quality):
    seed(quality)
    result = quality.client.get(api.API + '/form?month=2026-10').json()
    assert result['cells']['5']['M19']['text'] == ''
    assert result['cells']['5']['M20']['text'] == ''
    assert '6' not in result['cells']
    assert result['cells']['요약']['F12']['text'] == ''
    assert result['cells']['요약']['F13']['text'] == ''
    assert result['cells']['2-1']['M16']['text'] == ''
    assert result['cells']['4-1']['M15']['text'] == ''


def test_unclassified_assembly_is_visible_without_changing_process_totals(quality):
    from models.production_sync import ExternalProductionRecord
    from services import production_sync_service as sync
    seed(quality)
    with quality.sessions() as db:
        now=sync.now()
        db.add(ExternalProductionRecord(source_key='UNCLASSIFIED',lot_no='UNCLASSIFIED',job_no='UNCLASSIFIED',
            part_no='UNKNOWN-C',process_name='조립',work_date='2026-10-05',job_qty='99',lot_qty='100',fault_qty='1',
            raw_json='{"F10":"0","DEFECTS":[{"code":"F1","name":"찍힘","qty":1}]}',
            content_hash='UNCLASSIFIED',first_seen_at=now,last_seen_at=now,changed_at=now))
        db.commit()
    result=quality.client.get(api.API+'/form?month=2026-10').json()
    assert result['cells']['2-1']['L26']['text']=='1,300'
    assert result['cells']['2-1']['L27']['text']=='25'
    assert result['cells']['2-1']['L31']['text']=='300'
    assert result['cells']['2-1']['L32']['text']=='6'
    assert any('최종검사 품목군 미연결: UNKNOWN-C' in issue for issue in result['issues'])


def test_form_and_excel_use_identical_values_and_zero_is_not_missing(quality):
    seed(quality)
    manual = blank_manual()
    manual.update(delivery_qty=10000, customer_bad=0, customer_cases=0, revenue=1000000)
    for amounts in manual['claims'].values():
        amounts.update(occurred=0, agreed=0, deducted=0)
    assert quality.client.put(api.API,json={'month':'2026-10','version':0,'manual':manual}).status_code==200
    result = quality.client.get(api.API + '/form?month=2026-10').json()
    assert result['cells']['5']['M20']['text'] == '0'
    assert '6' not in result['cells']
    assert result['cells']['요약']['F12']['text'] == '0PPM'
    excel = quality.client.get(api.API + '/excel?month=2026-10')
    book = load_workbook(BytesIO(excel.content),data_only=True,read_only=True)
    for sheet,address in [('요약','F9'),('요약','F11'),('1-1','L20'),('요약','F13'),('5','M20')]:
        cell=book[sheet][address]
        assert result['cells'][sheet][address]['text']==format_cell(cell.value,cell.number_format)
    book.close()


def test_chart_literals_are_bound_to_report_cells(quality):
    seed(quality)
    result=quality.client.get(api.API+'/form?month=2026-10').json()
    goal=[s for s in result['charts']['xl/charts/chart11.xml']['series'] if '목표' in s['name']]
    assert len(goal)==1
    assert goal[0]['values']==[.003]*12
    for name in ('chart7.xml','chart8.xml'):
        assert result['charts']['xl/charts/'+name]['series'][0]['categories']==['찍힘','긁힘','치수','파손,휨','변색,외관','기타']


def test_report_router_registers_form_api_and_enforces_access(quality):
    app=FastAPI();app.include_router(api.router)
    app.dependency_overrides.update(quality.app.dependency_overrides)
    client=TestClient(app)
    page=client.get('/quality/performance')
    assert page.status_code==200
    assert 'id="qualityFormMonth"' in page.text
    assert client.get(api.API+'/form?month=2026-10').status_code==200
    assert client.get(api.API+'/form?month=2026-11').status_code==422
    assert client.get(api.API+'/form?month=2026-10&source=INVALID').status_code==422
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions='{}')
    assert client.get(api.API+'/form?month=2026-10').status_code==403


@pytest.mark.parametrize(('value','fmt','expected'),[(.003,'0.00%','0.30%'),(63579984,'#,##0,','63,580'),(0,'0.00%','0.00%'),(None,'#,##0',''),(1234,'#,##0','1,234'),(1.2,'0.00%','120.00%')])
def test_original_number_formats(value,fmt,expected):
    assert format_cell(value,fmt)==expected


def test_real_app_registers_september_screen_form_and_download(tmp_path):
    """Exercise app.py itself in an isolated copy, without touching the local DB."""
    from pathlib import Path
    import os
    import shutil
    import subprocess
    import sys
    root=Path(__file__).resolve().parents[1]
    copy=tmp_path/'application'
    copy.mkdir()
    for directory in ('core','models','routers','services','schemas','assets','static','templates'):
        shutil.copytree(root/directory,copy/directory,ignore=shutil.ignore_patterns('__pycache__'))
    for source in root.glob('*.py'):
        shutil.copy2(source,copy/source.name)
    script='''
from core import security
security.DEV_BYPASS_AUTH=True
import app
from fastapi.testclient import TestClient
client=TestClient(app.app)
screen=client.get('/quality/performance')
assert screen.status_code==200,screen.text
assert 'id="qualityFormLoad"' in screen.text
assert 'quality_performance_form.js' in screen.text
response=client.get('/api/quality/performance/form?month=2026-09&source=EXTERNAL')
assert response.status_code==200,response.text
assert response.json()['month']=='2026-09'
assert len(response.json()['cells'])==16
assert len(response.json()['charts'])==27
download=client.get('/api/quality/performance/excel?month=2026-09&source=EXTERNAL')
assert download.status_code==200,download.text[:200]
assert download.content.startswith(b'PK')
assert 'get' in app.app.openapi()['paths']['/api/quality/performance/form']
print('September screen/API/download: OK')
'''
    env=dict(os.environ,DATABASE_URL='sqlite:///'+str(tmp_path/'isolated.db'))
    result=subprocess.run([sys.executable,'-c',script],cwd=copy,env=env,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'September screen/API/download: OK' in result.stdout
