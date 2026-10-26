import pytest
from services.revision_number_service import display_revision_code,normalize_revision_code,revision_key


@pytest.mark.parametrize('stored,expected',[
    (0,'Rev.00'),('0','Rev.00'),('REV.0','Rev.00'),('Rev.00','Rev.00'),
    ('1','Rev.01'),('REV.001','Rev.01'),('rev. 9','Rev.09'),('REV.10','Rev.10'),
    ('REV.100','Rev.100'),('123456789012345678901234567890','Rev.123456789012345678901234567890'),
    ('A-01','A-01'),('REV.A','REV.A'),('',''),(None,'')])
def test_revision_display_preserves_identity_and_large_numbers(stored,expected):
    assert display_revision_code(stored)==expected
    if stored is not None and expected.startswith('Rev.'):
        assert revision_key(str(stored))==revision_key(expected)


def test_display_does_not_change_existing_normalization_contract():
    assert normalize_revision_code('REV.0')=='REV.0'
    assert normalize_revision_code('003')=='REV.003'
    assert display_revision_code('REV.003')=='Rev.03'


def test_fmea_print_formats_current_source_and_history_revisions():
    from routers.process_fmea import templates
    rendered=templates.get_template('standard_documents/fmea_print.html').render(
        fmea={'document_no':'TEST','revision_code':'REV.0','flow_revision_code':'1',
              'basis_revision_snapshot':'REV.002','rows':[]},
        history=[{'revision_code':'REV.0'},{'revision_code':'1'}],rows=[],status_label='초안')
    assert 'Rev.00' in rendered and 'Rev.01' in rendered and 'Rev.02' in rendered
    assert 'REV.0' not in rendered


def test_current_drawing_suppresses_format_only_original_revision_difference():
    from routers.documents import templates
    rendered=templates.get_template('current_drawing_view.html').render(
        item={'part_no':'PART','part_name':'부품'},revision={'revision_code':'REV.0'},
        documents=[{'title':'도면','document_no':'D1','document_revision':'00','files':[]}])
    assert '현재 사용 도면 개정번호: Rev.00' in rendered
    assert '기존 파일 표기 개정번호' not in rendered


def test_quality_report_print_uses_shared_revision_display():
    from routers.quality_reports import templates
    rendered=templates.get_template('quality_outbound_report_print.html').render(
        report={'report_no':'TEST','standard_revision':'REV.3','items':[],'judgment':'HOLD'})
    assert 'Rev.03' in rendered and 'REV.3' not in rendered
