"""Packing totals use source rows only; subtotal rows are presentation metadata."""
from itertools import groupby

SORT_LABELS = {'part_no':'품번','part_name':'품명','lot_no':'LOT 번호','packing_date':'포장일자',
    'packing_qty':'포장수량','shipment_qty':'출고수량','shipment_date':'출고일',
    'customer_name':'거래처','stock_qty':'재고수량','source_label':'구분 / 확인사항'}
NUMERIC_FIELDS = {'packing_qty','shipment_qty','stock_qty'}


def packing_value(row, field):
    if field == 'stock_qty':
        return (row.get('stock_qty') if row['record_source']=='EXTERNAL'
                else max(row['packing_qty']-row['shipment_qty'],0))
    if field == 'source_label':
        return ('외부 연동'+(' · '+row['stock_note'] if row.get('stock_note') else '')
                if row['record_source']=='EXTERNAL' else 'MES')
    return row.get(field)


def totals(rows):
    result = {}
    for field in NUMERIC_FIELDS:
        values = [packing_value(row,field) for row in rows]
        result[field] = None if any(v is None for v in values) else sum(values)
    return result


def packing_summaries(items, sort_fields, subtotal_fields, limit):
    summaries = []
    def key(row, field):
        value=packing_value(row,field)
        return None if value in (None,'') else float(value) if field in NUMERIC_FIELDS else str(value).casefold()
    for level,field in enumerate(sort_fields):
        if field not in subtotal_fields:
            continue
        prefix=sort_fields[:level+1]
        for _, group in groupby(enumerate(items), key=lambda pair: tuple(key(pair[1],f) for f in prefix)):
            members=list(group)
            start,end=members[0][0],members[-1][0]
            if start>=limit:
                continue
            context=[{'field':f,'name':SORT_LABELS[f],'value':packing_value(members[0][1],f)} for f in prefix]
            summaries.append({'after_index':min(end,limit-1),'level':level,'field':field,'context':context,
                'count':len(members),'truncated':end>=limit,'totals':totals([r for _,r in members])})
    summaries.sort(key=lambda s:(s['after_index'],-s['level']))
    return {'totals':totals(items),'subtotals':summaries}
