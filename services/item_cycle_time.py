import math
from fastapi import HTTPException
from models.production_cycle_time import ProductionCycleTime


def save_item_cycle(db,item_id,value):
    row=db.get(ProductionCycleTime,item_id)
    if value is None or value=='':
        if row:db.delete(row)
        return
    try:seconds=float(value)
    except (ValueError,TypeError):raise HTTPException(422,'사이클타임은 0보다 큰 초 단위 숫자로 입력하세요.')
    if not math.isfinite(seconds) or not 0<seconds<=86400:raise HTTPException(422,'사이클타임은 0보다 크고 86,400 이하인 숫자로 입력하세요.')
    if row is None:row=ProductionCycleTime(item_id=item_id);db.add(row)
    row.seconds=seconds
