from datetime import date,datetime,time,timedelta
import calendar
import holidays
from fastapi import HTTPException
from models.production_month_plan import ProductionMonthPlanLine, ProductionMonthPlanDay, ProductionPlanWorkTime
from models.equipment import EquipmentMaster
from sqlalchemy import func


def month_days(month):
    try:
        first=date.fromisoformat(month+'-01')
        if first.strftime('%Y-%m')!=month:raise ValueError()
    except ValueError:raise HTTPException(422,'올바른 조회 월을 선택하세요.')
    return [first+timedelta(days=i) for i in range(calendar.monthrange(first.year,first.month)[1])]


def default_day(day,public_days):
    return dict(work_date=day.isoformat(),working=day.weekday()<5 and day not in public_days,
                start_time='08:00',end_time='08:00',next_day=True,
                holiday=public_days.get(day,''),custom=False)


def days(db,process_code,first,last,equipment_id=None):
    public_days=holidays.KR(years=range(first.year,last.year+1),language='ko')
    query=db.query(ProductionMonthPlanDay.work_date,func.sum(ProductionMonthPlanDay.quantity)).join(ProductionMonthPlanLine,ProductionMonthPlanLine.id==ProductionMonthPlanDay.line_id).join(EquipmentMaster,EquipmentMaster.id==ProductionMonthPlanLine.equipment_id).filter(EquipmentMaster.process_code==process_code,ProductionMonthPlanDay.work_date>=first.isoformat(),ProductionMonthPlanDay.work_date<=last.isoformat())
    if equipment_id is not None:query=query.filter(ProductionMonthPlanLine.equipment_id==equipment_id)
    quantities=dict(query.group_by(ProductionMonthPlanDay.work_date).all())
    saved_times={}
    if equipment_id is not None:
        saved_times={r.work_date:r for r in db.query(ProductionPlanWorkTime).filter(ProductionPlanWorkTime.equipment_id==equipment_id,ProductionPlanWorkTime.work_date>=first.isoformat(),ProductionPlanWorkTime.work_date<=last.isoformat())}
    result=[]
    while first<=last:
        value=default_day(first,public_days)
        value.update(working=float(quantities.get(first.isoformat(),0) or 0)>0,custom=True,plan_managed=True)
        saved=saved_times.get(first.isoformat())
        if saved:value.update(start_time=saved.start_time,end_time=saved.end_time,next_day=saved.next_day)
        result.append(value);first+=timedelta(days=1)
    return result


def bounds(value):
    if not value['working']:return None
    day=date.fromisoformat(value['work_date'])
    try:
        start=datetime.combine(day,time.fromisoformat(value['start_time']))
        end=datetime.combine(day+timedelta(days=int(value['next_day'])),time.fromisoformat(value['end_time']))
    except ValueError:raise HTTPException(422,'가동 시작·종료시간을 올바르게 입력하세요.')
    if end<=start:raise HTTPException(422,value['work_date']+': 종료시간은 시작시간보다 늦어야 합니다. 익일 여부를 확인하세요.')
    return start,end


def workday_intervals(db,process_code,first,last,now,equipment_id=None):
    # The production date and schedule date both identify the shift's start day.
    rows=days(db,process_code,first,last,equipment_id)
    result=[]
    for value in rows:
        period=bounds(value)
        if period:
            a,b=period[0],min(period[1],now)
            if b>a:result.append((a,b))
    return result,[r for r in rows if r['holiday']]
