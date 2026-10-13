import io
import json
from types import SimpleNamespace
import pytest
from openpyxl import load_workbook
from test_production_sync import setup,DAY
from test_production_daily_sync import daily_response
from services.production_daily_sync import parse_daily,MACHINES,SHIFTS,DATA
from services import production_sync_client as source,production_sync_service as sync
from models.production_sync import ProductionSyncFailureData,ExternalProductionRecord,ProductionSyncRun
from core.security import get_current_user


def test_failed_query_snapshot_and_excel_show_original_blank_lot_without_credentials(setup,monkeypatch):
    values=daily_response()
    values[DATA+'LOT_NUM']=['']
    values[DATA+'PRODUCT_NM']=['=1+1']
    values['Common.Set.LoginUserSet.USER_PASS']=['private-password']
    values[DATA+'USER_PASS']=['private-password']
    values['THROW.STACK']=['private SQL and cookie']
    class Client:
        def __init__(self,*_):pass
        def fetch_day(self,day):return parse_daily(values,day,MACHINES[0],SHIFTS[0])
    monkeypatch.setattr(sync,'ProductionClient',Client)
    token,run_id=sync.claim(DAY,DAY,'MANUAL');sync.execute(DAY,DAY,token,run_id)
    with setup.sessions() as db:
        assert db.get(ProductionSyncRun,run_id).status=='FAILED'
        snapshot=db.get(ProductionSyncFailureData,run_id)
        assert snapshot is not None and 'private' not in snapshot.data_json
        data=json.loads(snapshot.data_json)
        assert data['equipment']=='LT-01' and data['fields'][DATA+'LOT_NUM']==['']
        assert db.query(ExternalProductionRecord).count()==0
    runs=setup.client.get('/api/production/external-sync/runs').json()
    assert runs[0]['has_error_data'] is True
    response=setup.client.get(f'/api/production/external-sync/runs/{run_id}/error-excel')
    assert response.status_code==200 and 'attachment' in response.headers['content-disposition']
    workbook=load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames==['조회 및 오류','원본 항목','쉼표 분리 참고']
    rows=list(workbook['원본 항목'].iter_rows(min_row=2,values_only=True))
    lot=next(r for r in rows if r[0]==DATA+'LOT_NUM')
    assert lot[1]=='공란' and lot[3]==0
    product=next(cells[5] for cells in workbook['원본 항목'].iter_rows(min_row=2) if cells[0].value==DATA+'PRODUCT_NM')
    assert product.value=='=1+1' and product.data_type=='s'
    assert all('private' not in str(cell.value) for sheet in workbook for cells in sheet for cell in cells)
    setup.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(username='reader',role='USER',permissions='{}')
    assert setup.client.get(f'/api/production/external-sync/runs/{run_id}/error-excel').status_code==403


def test_excel_preserves_long_comma_note_chunks_and_does_not_claim_row_alignment():
    from services.production_sync_error_excel import build_error_excel
    note=('raw note, with punctuation\n'*1800)
    data={'work_date':DAY.isoformat(),'equipment':'LT-01','error':'bad count','fields':{DATA+'PRODUCT_NM':[note]}}
    workbook=load_workbook(io.BytesIO(build_error_excel(data)))
    rows=[r for r in workbook['원본 항목'].iter_rows(min_row=2,values_only=True) if r[0]==DATA+'PRODUCT_NM']
    assert ''.join(r[5] or '' for r in rows)==note
    assert len(rows)>1
    missing=next(r for r in workbook['원본 항목'].iter_rows(min_row=2,values_only=True) if r[0]==DATA+'LOT_NUM')
    assert missing[1]=='미수신'
    assert '행 연결 미확정' in workbook['쉼표 분리 참고']['E1'].value


def test_existing_history_without_snapshot_has_no_download(setup):
    token,run_id=sync.claim(DAY,DAY,'MANUAL')
    assert setup.client.get('/api/production/external-sync/runs').json()[0]['has_error_data'] is False
    response=setup.client.get(f'/api/production/external-sync/runs/{run_id}/error-excel')
    assert response.status_code==404 and '다시 가져오면' in response.json()['detail']
