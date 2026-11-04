import json
from decimal import Decimal


def assess(data):
    flags=[];groups={};lines=[]
    if not data['materials']:flags.append('자재 기록 없음')
    for line in data['materials']:
        key=(line['part_no'].casefold(),line['unit'].casefold())
        group=groups.setdefault(key,{'part_no':line['part_no'],'unit':line['unit'],'expected_qty':None,'unit_usage':None,'issued_qty':Decimal(0),'issued_complete':True,'used_qty':Decimal(0),'complete':True})
        if line.get('expected_qty') is not None:group['expected_qty']=Decimal(line['expected_qty'])
        if line.get('unit_usage') is not None:group['unit_usage']=Decimal(line['unit_usage'])
        if line.get('issued_qty') is None:group['issued_complete']=False
        else:group['issued_qty']+=Decimal(line['issued_qty'])
        if line.get('used_qty') is None:group['complete']=False
        else:group['used_qty']+=Decimal(line['used_qty'])
        fields=['issued_qty','used_qty','scrap_qty','remaining_qty']
        complete=all(line.get(key) is not None for key in fields)
        balance=sum((Decimal(line[key])*(1 if key=='issued_qty' else -1) for key in fields),Decimal(0)) if complete else None
        if not complete and any(line.get(k) is not None for k in ['used_qty','scrap_qty','remaining_qty']):flags.append('수량 미입력')
        elif complete and balance!=0:flags.append('수불 불일치')
        lines.append(line|{'balance_difference':str(balance) if balance is not None else None})
    summaries=[]
    for group in groups.values():
        unit_usage=group['unit_usage'] if group['unit_usage'] is not None else Decimal(1)
        expected=group['expected_qty'] if group['expected_qty'] is not None else Decimal(data['production_qty'])*unit_usage
        issued=group['issued_qty'] if group['issued_complete'] else None
        used=group['used_qty'] if group['complete'] else None
        difference=used-expected if used is not None and expected is not None else None
        if issued is None:flags.append('투입수량 미입력')
        elif issued<expected:flags.append('투입 부족')
        elif issued>expected:flags.append('과다 투입')
        if used is not None and used<expected:flags.append('사용 0 · 누락 확인' if used==0 else '사용 부족')
        elif used is not None and used>expected:flags.append('과다 사용')
        summaries.append({k:v for k,v in group.items() if k not in ('expected_qty','used_qty','complete','unit_usage','issued_qty','issued_complete')}|{
            'unit_usage':str(unit_usage),'issued_qty':str(issued) if issued is not None else None,
            'issued_difference':str(issued-expected) if issued is not None else None,
            'issued_rate':str(issued/expected*100) if issued is not None and expected else None,
            'expected_qty':str(expected) if expected is not None else None,'used_qty':str(used) if used is not None else None,
            'difference':str(difference) if difference is not None else None})
    return {'flags':list(dict.fromkeys(flags)) or ['정상(기록 기준)'],'materials':lines,'summary':summaries}


def serialize(record):
    data=json.loads(record.data_json)
    return data|assess(data)|{'id':record.id,'version':record.version,'cancelled':record.cancelled,
        'created_by':record.created_by,'updated_by':record.updated_by,'updated_at':record.updated_at.isoformat(timespec='seconds')}
