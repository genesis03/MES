import json
from io import BytesIO
import pytest
from openpyxl import load_workbook
from test_quality_performance import quality,seed
from test_production_sync import setup
from models.production_sync import ExternalProductionRecord
from routers import quality_performance as api


@pytest.mark.parametrize('shared,moved',[(False,2),(False,6),(False,0),(True,4)])
@pytest.mark.parametrize('code',['F7','F6'])
def test_assembly_misassembly_is_added_to_process_without_changing_final_totals(quality,shared,moved,code):
    seed(quality)
    with quality.sessions() as db:
        records=db.query(ExternalProductionRecord).filter_by(process_name='조립').order_by(ExternalProductionRecord.id).all()
        if shared:
            records=records[:2]
            details=[{'code':code,'name':'오조립','qty':4},{'code':'F1','name':'찍힘','qty':10}]
            lots=[r.lot_no for r in records]
            for r in records:
                raw=json.loads(r.raw_json);raw.update(DEFECTS=[],ASSEMBLY_GROUP_LOTS=lots,ASSEMBLY_GROUP_DEFECTS=details);r.raw_json=json.dumps(raw)
            # Both rows have different final groups: give them one known family.
            records[0].part_no=records[1].part_no
        else:
            r=records[0];raw=json.loads(r.raw_json)
            raw['DEFECTS']=[{'code':code,'name':'오조립','qty':moved},{'code':'F1','name':'찍힘','qty':6-moved}];r.raw_json=json.dumps(raw)
        db.commit()
    data=quality.client.get(api.API+'?month=2026-10').json();m=data['months'][9]
    assert m['assembly_process_bad']==moved
    assert m['process_types'].get('오조립',0)==moved
    assert data['overview'][9]['process_bad']==6+moved
    assert data['overview'][9]['final_bad']==24
    assert sum(m['process_types'].values())==6+moved
    assert sum(m['final_types'].values())==24
    assert '오조립' not in m['final_types']
    assert m['final_types'].get('기타' if code=='F7' else '소재',0)==moved
    assert m['final_types']['찍힘']==24-moved
    book=load_workbook(BytesIO(quality.client.get(api.API+'/excel?month=2026-10').content),data_only=True)
    assert book['2-2']['K25'].value==moved
    assert book['2-1']['L32'].value==book['2-2']['K19'].value==6+moved
    assert book['4-1']['L29'].value==book['4-2']['K19'].value==24
    assert book['2']['M20'].value==6+moved
    assert book['4']['M20'].value==24
    assert book['2-1']['L27'].value==moved
    assert book['2-1']['O27'].value==moved
    assert book['2-1']['L32'].value==sum(book['2-1'][f'L{row}'].value for row in (17,22,27))
    book.close()


def test_shared_mixed_products_keep_existing_final_breakdown(quality):
    seed(quality)
    with quality.sessions() as db:
        rows=db.query(ExternalProductionRecord).filter_by(process_name='조립').order_by(ExternalProductionRecord.id).all()[:2]
        lots=[row.lot_no for row in rows]
        for row in rows:
            raw=json.loads(row.raw_json)
            raw.update(DEFECTS=[],ASSEMBLY_GROUP_LOTS=lots,ASSEMBLY_GROUP_DEFECTS=[{'code':'F7','name':'오조립','qty':4},{'code':'F1','name':'찍힘','qty':10}])
            row.raw_json=json.dumps(raw)
        db.commit()
    result=quality.client.get(api.API+'?month=2026-10').json()
    assert result['overview'][9]['process_bad']==10
    assert result['overview'][9]['final_bad']==24
    assert sum(result['months'][9]['final_types'].values())==24
    assert result['months'][9]['process_types']['오조립']==4
