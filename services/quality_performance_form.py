"""Project the same populated workbook used for download onto the original HTML form."""
from datetime import date, datetime
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import re
from zipfile import ZipFile

from lxml import etree as ET
from openpyxl import load_workbook

from services.quality_performance_excel import build_excel, NS

FORM = Path(__file__).resolve().parent.parent / 'static/quality_performance_form.json'
DRAWING_NS = {'x':'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing',
              'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}


@lru_cache(maxsize=1)
def form_definition():
    return json.loads(FORM.read_text(encoding='utf-8'))


def format_cell(value, number_format):
    if value is None or value == '':
        return ''
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y. %m. %d.')
    if not isinstance(value, (int, float)):
        return str(value)
    sections = number_format.split(';')
    section = sections[1] if value < 0 and len(sections) > 1 else sections[2] if value == 0 and len(sections) > 2 else sections[0]
    if not section:
        return ''
    if value == 0 and '"-"' in section:
        return '-'
    if number_format == 'General':
        return str(int(value)) if float(value).is_integer() else f'{value:.6g}'
    pattern = re.sub(r'"[^"]*"|\[[^\]]*\]|_.|\*.', '', section)
    percent = '%' in pattern
    scaled = value * 100 if percent else value
    trailing = re.search(r'[0#](,+)(?:\s|$)', pattern)
    if trailing:
        scaled /= 1000 ** len(trailing[1])
    precision = re.search(r'\.([0#]+)', pattern)
    digits = len(precision[1]) if precision else 0
    grouped = ',' in pattern[:pattern.find('.') if '.' in pattern else len(pattern)].rstrip(', ')
    text = format(abs(scaled) if value < 0 and ('▼' in section or '(' in section) else scaled, f'{"," if grouped else ""}.{digits}f')
    if percent:
        text += '%'
    if 'PPM' in section.upper():
        text += 'PPM' if 'PPM' in section else 'ppm'
    if '▲' in section and value > 0:
        text = '▲' + text
    elif '▼' in section:
        text = '▼' + text
    elif value < 0 and '(' in section:
        text = '(' + text + ')'
    return text


def _points(parent, numeric=False):
    if parent is None:
        return []
    cache = next((n for n in parent.iter() if ET.QName(n).localname in ('numCache','strCache','numLit','strLit')), None)
    if cache is None:
        return []
    count = cache.find('c:ptCount', NS)
    values = [None] * int(count.get('val', '0') if count is not None else 0)
    for point in cache.findall('c:pt', NS):
        index = int(point.get('idx'))
        while len(values) <= index:
            values.append(None)
        value = point.findtext('c:v', namespaces=NS)
        values[index] = float(value) if numeric and value not in (None, '') else value
    return values


def chart_data(root):
    ns = dict(NS, a=DRAWING_NS['a'])
    series = []
    for node in root.xpath('.//c:ser', namespaces=NS):
        values = _points(node.find('c:val', NS), True)
        names = _points(node.find('c:tx', NS))
        name = names[0] if names else node.findtext('c:tx/c:v', default='', namespaces=NS)
        colors = node.xpath('./c:spPr/a:solidFill/a:srgbClr/@val|./c:spPr/a:ln/a:solidFill/a:srgbClr/@val', namespaces=ns)
        reference = node.find('c:val/c:numRef/c:numCache/c:formatCode', NS)
        fmt = reference.text if reference is not None else ''
        categories = _points(node.find('c:cat', NS))
        kind = ET.QName(node.getparent()).localname
        axis = node.getparent().find('c:axId', NS)
        series.append({'name':name or '', 'categories':categories, 'values':values,
                       'kind':kind, 'axis':axis.get('val') if axis is not None else '',
                       'percent':'%' in (fmt or ''), 'color':'#'+colors[0] if colors else None})
    axes = {}
    for axis in root.xpath('.//c:valAx', namespaces=NS):
        identifier = axis.find('c:axId', NS)
        number_format = axis.find('c:numFmt', NS)
        if identifier is not None:
            axes[identifier.get('val')] = '%' in (number_format.get('formatCode','') if number_format is not None else '')
    for item in series:
        # Excel chart groups list category then value axis; look up the group's value axis.
        node_kind = item['kind']
        for group in root.xpath('.//c:plotArea/*', namespaces=NS):
            if ET.QName(group).localname == node_kind:
                value_axes = [a.get('val') for a in group.findall('c:axId',NS) if a.get('val') in axes]
                if value_axes:
                    item['axis'] = value_axes[-1]
                    item['percent'] = axes[value_axes[-1]]
                break
    return {'series':series}


def build_form(report, writing_date, author):
    workbook = build_excel(report, writing_date, author).getvalue()
    sheets = load_workbook(BytesIO(workbook), data_only=True, read_only=True)
    definitions = form_definition()['sheets']
    result = {'month':report['month'],'source':report['source'],'cells':{},'charts':{},'drawings':{},
              'issues':report['issues'], 'manual_pending':['납품수량','고객불량','매출액','클레임'],
              'reference_note':'2024·2025년 비교 실적은 제공된 원본 엑셀의 참고값입니다.' if report['month'].startswith('2026-') else ''}
    try:
        for definition in definitions:
            wanted = {cell['address'] for cell in definition['cells']}
            values = {}
            for row in sheets[definition['name']].iter_rows():
                for cell in row:
                    if getattr(cell,'coordinate',None) not in wanted:
                        continue
                    value = cell.value
                    if cell.data_type == 'e':
                        value = None
                    text = format_cell(value, cell.number_format)
                    values[cell.coordinate] = {'text':text,'numeric':isinstance(value,(int,float))}
                    if isinstance(value,(int,float)) and cell.number_format.startswith('[Red][>0]'):
                        values[cell.coordinate]['color'] = '#ff0000' if value > 0 else '#0000ff' if value < 0 else '#000000'
            result['cells'][definition['name']] = values
    finally:
        sheets.close()
    with ZipFile(BytesIO(workbook)) as package:
        for definition in definitions:
            for chart in definition['charts']:
                part = chart['part']
                result['charts'][part] = chart_data(ET.fromstring(package.read(part)))
        for i, definition in enumerate(definitions, 1):
            path = f'xl/drawings/drawing{i}.xml'
            if path not in package.namelist():
                continue
            root = ET.fromstring(package.read(path))
            result['drawings'][definition['name']] = [
                ''.join(anchor.xpath('.//x:sp/x:txBody//a:t/text()',namespaces=DRAWING_NS))
                for anchor in root if anchor.find('x:from',DRAWING_NS) is not None
                and anchor.find('x:to',DRAWING_NS) is not None
                and anchor.xpath('.//x:sp/x:txBody//a:t/text()',namespaces=DRAWING_NS)
            ]
    return result
