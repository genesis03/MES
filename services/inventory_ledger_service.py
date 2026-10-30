import calendar
from datetime import date, timedelta
from decimal import Decimal
from models.inventory_ledger import InventoryLedgerBalance
from services.inventory_ledger_events import events_for_month


def month_bounds(month):
    first = date.fromisoformat(month+'-01')
    return first.isoformat(), first.replace(day=calendar.monthrange(first.year,first.month)[1]).isoformat()


def text_qty(value):
    return None if value is None else format(value, 'f')


def report(db, month, source, part_no='', category=''):
    first,last = month_bounds(month)
    events,warnings,connections = events_for_month(db,first,last,source)
    balances = {row.item_id:row for row in db.query(InventoryLedgerBalance).filter_by(month=month,source=source)}
    grouped = {}
    for event in events:
        key = event['item_id'] if event['item_id'] is not None else (event['raw_part'],event['process'])
        grouped.setdefault(key,[]).append(event)
    keys = set(grouped) | set(balances) | {r.id for r in connections.items if r.is_active=='Y'}
    rows = []
    for key in keys:
        item = connections.by_id.get(key) if isinstance(key,int) else None
        part = item.part_no if item else key[0]
        kind = item.material_type if item else '미연결'
        if part_no and part_no.casefold() not in part.casefold() and (not item or part_no.casefold() not in (item.part_name or '').casefold()):continue
        if category and kind != category:continue
        evidence = grouped.get(key,[])
        balance = balances.get(key)
        opening = balance.opening_qty if balance else None
        received = sum((e['qty'] for e in evidence if e['direction']=='IN'),Decimal(0))
        issued = sum((e['qty'] for e in evidence if e['direction']=='OUT'),Decimal(0))
        closing = opening+received-issued if opening is not None else None
        actual = balance.actual_qty if balance else None
        carry = balance.count_carry_qty if balance else Decimal(0)
        expected_at_count = None
        if opening is not None and balance and balance.count_date:
            expected_at_count = opening + sum((e['qty'] if e['direction']=='IN' else -e['qty'] for e in evidence if e['date']<=balance.count_date),Decimal(0))
        totals = {}
        for e in evidence:
            col=e['category']+' / '+('입고' if e['direction']=='IN' else '출고')
            totals[col]=totals.get(col,Decimal(0))+e['qty']
        stage_values = {field:text_qty(totals.get(label,Decimal(0))) for field,label in {
            'production_qty':'생산 / 입고','production_defect_qty':'생산불량 / 출고',
            'setup_qty':'SET-UP / 출고','subcontract_in_qty':'외주입고 / 입고',
            'subcontract_defect_qty':'외주불량 / 출고','loss_qty':'LOSS / 출고',
            'sample_qty':'샘플 / 출고','packing_qty':'포장 / 입고','raw_in_qty':'구매입고 / 입고'}.items()}
        rows.append(dict(**stage_values,item_id=item.id if item else None,key=str(key),part_no=part,
                         part_name=(item.part_name or '') if item else '',category=kind,
                         category_name={'RAW':'원소재','SEMI':'재공품','FINISHED':'완제품','PART':'부품','PACKAGING':'포장재'}.get(kind,kind),
                         process='' if item else key[1],unit=(item.unit or 'EA') if item else '미확인',
                         opening_qty=text_qty(opening),in_qty=text_qty(received),out_qty=text_qty(issued),
                         closing_qty=text_qty(closing),actual_qty=text_qty(actual),count_carry_qty=text_qty(carry),actual_total_qty=text_qty(actual+carry) if actual is not None else None,count_date=balance.count_date if balance else None,
                         expected_at_count=text_qty(expected_at_count),
                         difference=text_qty(actual+carry-expected_at_count) if actual is not None and expected_at_count is not None else None,
                         note=balance.note if balance else '',version=balance.version if balance else 0,
                         totals={k:text_qty(v) for k,v in totals.items()},
                         event_count=len(evidence),linked=bool(item)))
    return dict(month=month,source=source,first=first,last=last,rows=sorted(rows,key=lambda r:(r['part_no'],r['process'])),warnings=warnings),events


def detail(db,month,source,item_id=None,raw_part='',process=''):
    data,events = report(db,month,source)
    row = next((r for r in data['rows'] if (r['item_id']==item_id if item_id is not None else not r['linked'] and r['part_no']==raw_part and r['process']==process)),None)
    if row is None:return None
    selected = [e for e in events if (e['item_id']==item_id if item_id is not None else e['item_id'] is None and e['raw_part']==raw_part and e['process']==process)]
    daily=[]
    running=Decimal(row['opening_qty']) if row['opening_qty'] is not None else None
    day=date.fromisoformat(data['first'])
    while day.isoformat()<=data['last']:
        today=[e for e in selected if e['date']==day.isoformat()]
        incoming=sum((e['qty'] for e in today if e['direction']=='IN'),Decimal(0))
        outgoing=sum((e['qty'] for e in today if e['direction']=='OUT'),Decimal(0))
        if running is not None:running+=incoming-outgoing
        daily.append(dict(date=day.isoformat(),in_qty=text_qty(incoming),out_qty=text_qty(outgoing),balance=text_qty(running)))
        day+=timedelta(days=1)
    return dict(row=row,daily=daily,events=[e | {'qty':text_qty(e['qty'])} for e in selected])
