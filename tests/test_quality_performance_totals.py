"""Every summary quantity and defect breakdown shares the detailed totals."""
from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from services.quality_performance_service import new_month,GOALS
from services.quality_performance_excel import build_excel
from services.quality_performance_form import build_form


def populated_report():
    months=[new_month() for _ in range(12)]
    for index,m in enumerate(months):
        n=index+1
        m['raw'].update(COPPER=10*n,CAP=20*n,SEAL=30*n)
        m['process']['LATHE'].update(qty=100*n,bad=2*n)
        m['process']['TAP'].update(qty=200*n,bad=3*n)
        m['process_types'].update(찍힘=2*n,기타=3*n,오조립=n)
        m['assembly_process_bad']=n
        m['assembly'].update(qty=300*n,bad=4*n)
        m['subcontract']['메인텍'].update(qty=400*n,bad=n)
        m['subcontract']['혜성금속'].update(qty=500*n,bad=2*n)
        m['subcontract_types'].update(얼룩=n,박리=2*n)
        m['final']['BASE'].update(qty=100*n,bad=n)
        m['final']['SV1'].update(qty=200*n,bad=3*n)
        m['final_types'].update(찍힘=n,기타=3*n)
        m['delivery_qty']=1000*n
        m['manual'].update(customer_bad=n,customer_cases=1)
    return dict(month='2026-10',source='EXTERNAL',months=months,manual=months[9]['manual'],
                goals=GOALS,issues=[],raw_vendors={},version=0)


def test_all_months_summary_formulas_reference_detail_totals():
    output=build_excel(populated_report(),date(2026,11,1),'작성자').getvalue()
    formulas=load_workbook(BytesIO(output),data_only=False)
    values=load_workbook(BytesIO(output),data_only=True)
    for month in range(1,13):
        col=get_column_letter(month+3)
        for sheet,detail,start,qty,bad in [('1','1-1',3,28,29),('2','2-1',3,31,32),('3','3-1',4,22,23),('4','4-1',3,28,29)]:
            src=get_column_letter(month+start-1)
            for row,source_row in [(19,qty),(20,bad)]:
                assert formulas[sheet][f'{col}{row}'].value==f"='{detail}'!{src}{source_row}"
                if month<=10:
                    assert values[sheet][f'{col}{row}'].value==values[detail][f'{src}{source_row}'].value
    for sheet in ('1','2','3','4'):
        for row in (19,20):
            assert values[sheet][f'P{row}'].value==sum(values[sheet].cell(row,m+3).value for m in range(1,11))
    formulas.close();values.close()


def test_monthly_annual_and_html_defect_totals_match_type_breakdowns():
    report=populated_report()
    book=load_workbook(build_excel(report,date(2026,11,1),'작성자'),data_only=True)
    form=build_form(report,date(2026,11,1),'작성자')
    for month in range(1,11):
        c=get_column_letter(month+2);b=get_column_letter(month+1);d=get_column_letter(month+3)
        for detail,col,row,types in [('1-1',c,29,'1-2'),('2-1',c,32,'2-2'),('3-1',d,23,'3-2'),('4-1',c,29,'4-2')]:
            assert book[detail][f'{col}{row}'].value==book[types][f'{b}19'].value
        assert book['2-1'][f'{c}31'].value==sum(book['2-1'][f'{c}{r}'].value for r in (16,21,26))
        assert book['2-1'][f'{c}27'].value==month
        assert form['cells']['2-1'][f'{c}27']['text']==str(month)
        assert book['2-1'][f'{c}28'].value==pytest.approx(1/300)
        assert book['2-1'][f'{c}32'].value==sum(book['2-1'][f'{c}{row}'].value for row in (17,22,27))
        assert book['5'][f'{d}19'].value==1000*month
        assert book['5'][f'{d}22'].value==pytest.approx(1000)
        assert book['요약']['F12'].value==pytest.approx(1000)
    for detail,col,row,types in [('1-1','O',29,'1-2'),('2-1','O',32,'2-2'),('3-1','P',23,'3-2'),('4-1','O',29,'4-2')]:
        assert book[detail][f'{col}{row}'].value==book[types]['N19'].value
    assert book['2-1']['O27'].value==55
    assert book['2-1']['O28'].value==pytest.approx(1/300)
    assert book['2-1']['O32'].value==sum(book['2-1'][f'O{row}'].value for row in (17,22,27))
    assert book['요약']['F9'].value==pytest.approx(6/600)
    assert book['요약']['F10'].value==pytest.approx(3/900)
    assert book['요약']['F11'].value==pytest.approx(4/300)
    book.close()
