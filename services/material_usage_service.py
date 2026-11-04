import json
from decimal import Decimal


def assess(data):
    flags, groups, lines = [], {}, []
    production = Decimal(data['production_qty'])
    if not data['materials']:
        flags.append('자재 기록 없음')
    for index, line in enumerate(data['materials'], 1):
        key = (line['part_no'].casefold(), line['unit'].casefold())
        group = groups.setdefault(key, {'part_no': line['part_no'], 'unit': line['unit'],
            'issued_qty': Decimal(0), 'used_qty': Decimal(0), 'scrap_qty': Decimal(0),
            'remaining_qty': Decimal(0), 'missing': set(), 'balance_details': []})
        for field in ('issued_qty', 'used_qty', 'scrap_qty', 'remaining_qty'):
            if line.get(field) is None:
                group['missing'].add(field)
            else:
                group[field] += Decimal(line[field])
        complete = all(line.get(k) is not None for k in ('issued_qty', 'used_qty', 'scrap_qty', 'remaining_qty'))
        balance = Decimal(line['issued_qty'])-Decimal(line['used_qty'])-Decimal(line['scrap_qty'])-Decimal(line['remaining_qty']) if complete else None
        if balance is None:
            balance_text = '미판정 · 투입/사용/폐기/잔여 미입력'
            flags.append('수량 미입력')
        elif balance:
            balance_text = f"{abs(balance):f} {line['unit']} " + ('사용처 미기록' if balance > 0 else '투입 초과 기록')
            flags.append('수불 불일치')
        else:
            balance_text = '일치 · 차이 0'
        if balance is None or balance:
            group['balance_details'].append({'row': index, 'lot_no': line.get('lot_no', ''), 'difference': str(balance) if balance is not None else None, 'description': balance_text})
        lines.append(line | {'balance_difference': str(balance) if balance is not None else None, 'balance_description': balance_text})
    summaries = []
    for group in groups.values():
        missing = group.pop('missing')
        for field in missing:
            group[field] = None
        used = group['used_qty']
        difference = used-production if used is not None else None
        if used is None:
            description = '미판정 · 자재 사용수량 미입력'
            flags.append('자재 사용수량 미입력')
        elif difference < 0:
            description = f'생산 대비 자재 사용 {abs(difference):f} {group["unit"]} 부족'
            flags.append('생산 대비 자재 사용 부족')
        elif difference > 0:
            description = f'생산 대비 자재 사용 {difference:f} {group["unit"]} 초과'
            flags.append('생산 대비 자재 사용 초과')
        else:
            description = '생산 대비 자재 사용 일치 · 차이 0'
        summaries.append(group | {field: str(group[field]) if group[field] is not None else None for field in ('issued_qty','used_qty','scrap_qty','remaining_qty')} | {
            'production_qty': str(production), 'difference': str(difference) if difference is not None else None,
            'usage_rate': str(used/production*100) if used is not None and production else None,
            'usage_description': description})
    return {'flags': list(dict.fromkeys(flags)) or ['정상(기록 기준)'], 'materials': lines, 'summary': summaries}


def serialize(record):
    data=json.loads(record.data_json)
    return data|assess(data)|{'id':record.id,'version':record.version,'cancelled':record.cancelled,
        'created_by':record.created_by,'updated_by':record.updated_by,'updated_at':record.updated_at.isoformat(timespec='seconds')}


def daily_review(records):
    """Sum production once per record/material, regardless of issue line count."""
    groups = {}
    for record in records:
        if record['cancelled']:
            continue
        for material in record['summary']:
            key = (record['work_date'], record['part_no'].casefold(), material['part_no'].casefold(), material['unit'].casefold())
            group = groups.setdefault(key, {'work_date':record['work_date'], 'part_no':record['part_no'],
                'material_part':material['part_no'], 'unit':material['unit'], 'production_qty':Decimal(0),
                'used_qty':Decimal(0), 'issued_qty':Decimal(0), 'scrap_qty':Decimal(0), 'remaining_qty':Decimal(0),
                'missing':set(), 'balance_details':[], 'workers':set()})
            group['production_qty'] += Decimal(record['production_qty'])
            group['workers'].add(record['worker'])
            for field in ('used_qty','issued_qty','scrap_qty','remaining_qty'):
                if material[field] is None:group['missing'].add(field)
                else:group[field] += Decimal(material[field])
            group['balance_details'].extend(f"기록 {record['id']} / {d['row']}행 / LOT {d['lot_no'] or '미입력'}: {d['description']}" for d in material['balance_details'])
    result=[]
    for group in groups.values():
        for field in group.pop('missing'):group[field]=None
        used, production = group['used_qty'], group['production_qty']
        group['difference'] = used-production if used is not None else None
        group['usage_rate'] = used/production*100 if used is not None and production else None
        group['verdict'] = ('미판정 · 사용수량 미입력' if used is None else '미판정 · 생산수량 0' if not production
            else '조립 누락 확인 필요' if used < production else '자재 LOSS 증가 확인 필요' if used/production >= Decimal('1.05')
            else '생산 대비 자재 사용 초과' if used > production else '이상없음(기록 기준)')
        if group['balance_details']:group['verdict'] += ' / 수불 내역 확인'
        result.append(group)
    return sorted(result, key=lambda r:(r['work_date'],r['part_no'],r['material_part'],r['unit']))
