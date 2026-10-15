"""The same 12 defect categories plus Other for both quality reports."""
import re
from models.models import CommonCodeModel
from models.quality_migration import DEFECT_TYPE_SEED


def report_types(db):
    registered = {row.code: row for row in db.query(CommonCodeModel)
                  .filter(CommonCodeModel.group_code == 'DEFECT_TYPE')}
    defaults = list(DEFECT_TYPE_SEED) + [('13', '기타')]
    columns = [{'code': code, 'name': registered[code].code_name if code in registered else name}
               for code, name in defaults]
    return sorted(columns, key=lambda c: (registered[c['code']].sort_order if c['code'] in registered else int(c['code']), int(c['code'])))


def report_quantities(details, columns, total=None):
    values = {column['code']: 0. for column in columns}
    by_name = {column['name'].strip().casefold(): column['code'] for column in columns}
    by_name.update({name.casefold(): code for code, name in DEFECT_TYPE_SEED})
    by_name.update({'burr': '5', '칩 엉김': '4', '기타': '13'})
    for detail in details:
        code = str(detail.get('code') or '')
        # F1..F13 are external MES category codes, not the raw F10 setup field.
        match = re.fullmatch(r'(?:EXTERNAL:)?F(1[0-3]|[1-9])', code, re.IGNORECASE)
        if match:
            code = match.group(1)
        if code not in values:
            code = by_name.get(str(detail.get('name') or '').strip().casefold(), '13')
        values[code] += max(float(detail.get('qty') or 0), 0)
    if total is not None:
        values['13'] += max(float(total) - sum(values.values()), 0)
    return values
