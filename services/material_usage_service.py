import json
from decimal import Decimal


def assess(data):
    flags=[];groups={};lines=[]
    if not data['materials']:flags.append('자재 기록 없음')
    for line in data['materials']:
        key=(line['part_no'].casefold(),line['unit'].casefold())
        group=groups.setdefault(key,{'part_no':line['part_no'],'unit':line['unit'],'expected_qty':None,'used_qty':Decimal(0),'complete':True})
        if line.get('expected_qty') is not None:group['expected_qty']=Decimal(line['expected_qty'])
        if line.get('used_qty') is None:group['complete']=False
        else:group['used_qty']+=Decimal(line['used_qty'])
        fields=['issued_qty','used_qty','scrap_qty','remaining_qty']
        complete=all(line.get(key) is not None for key in fields)
        balance=sum((Decimal(line[key])*(1 if key=='issued_qty' else -1) for key in fields),Decimal(0)) if complete else None
        if not complete:flags.append('수량 미입력')
        elif balance!=0:flags.append('수불 불일치')
        lines.append(line|{'balance_difference':str(balance) if balance is not None else None})
    summaries=[]
    for group in groups.values():
        expected=group['expected_qty'];used=group['used_qty'] if group['complete'] else None
        difference=used-expected if used is not None and expected is not None else None
        if expected is None:flags.append('필요수량 미입력')
        elif used is not None and used<expected:flags.append('사용 0 · 누락 확인' if used==0 else '사용 부족')
        elif used is not None and used>expected:flags.append('과다 사용')
        summaries.append({k:v for k,v in group.items() if k not in ('expected_qty','used_qty','complete')}|{
            'expected_qty':str(expected) if expected is not None else None,'used_qty':str(used) if used is not None else None,
            'difference':str(difference) if difference is not None else None})
    return {'flags':list(dict.fromkeys(flags)) or ['정상(기록 기준)'],'materials':lines,'summary':summaries}


def serialize(record):
    data=json.loads(record.data_json)
    return data|assess(data)|{'id':record.id,'version':record.version,'cancelled':record.cancelled,
        'created_by':record.created_by,'updated_by':record.updated_by,'updated_at':record.updated_at.isoformat(timespec='seconds')}
