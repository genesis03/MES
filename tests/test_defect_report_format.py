from test_production_sync import setup
from services.defect_report_format import report_types, report_quantities


def test_both_reports_have_thirteen_columns_even_without_occurrences(setup):
    with setup.sessions() as db:
        columns = report_types(db)
    assert [c['code'] for c in columns] == [str(n) for n in range(1, 14)]
    assert columns[-1] == {'code': '13', 'name': '기타'}
    assert report_quantities([], columns) == {str(n): 0 for n in range(1, 14)}


def test_source_codes_share_columns_without_using_source_labels_as_new_columns(setup):
    with setup.sessions() as db:
        columns = report_types(db)
    values = report_quantities([
        {'code': '4', 'name': '칩', 'qty': 2},
        {'code': 'EXTERNAL:F4', 'name': '칩 엉김', 'qty': 3},
        {'code': 'D1', 'name': '치수', 'qty': 4},
        {'code': 'OTHER-UNKNOWN', 'name': '확인되지 않은 유형', 'qty': 1},
    ], columns, total=12)
    assert values['4'] == 5 and values['3'] == 4 and values['13'] == 3
    assert sum(values.values()) == 12
