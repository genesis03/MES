from datetime import date,datetime,time,timedelta
import calendar
from collections import Counter,defaultdict
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


def default_excluded_minutes(process_code):
    return 210 if process_code=='TP' else 0


def default_day(day,public_days):
    return dict(work_date=day.isoformat(),working=day.weekday()<5 and day not in public_days,
                start_time='08:00',end_time='08:00',next_day=True,
                holiday=public_days.get(day,''),custom=False,excluded_minutes=0)


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
        value.update(working=float(quantities.get(first.isoformat(),0) or 0)>0,custom=True,plan_managed=True,excluded_minutes=default_excluded_minutes(process_code))
        saved=saved_times.get(first.isoformat())
        if saved:value.update(start_time=saved.start_time,end_time=saved.end_time,next_day=saved.next_day,excluded_minutes=saved.excluded_minutes)
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


def net_hours(value):
    period=bounds(value | {'working':True})
    duration=(period[1]-period[0]).total_seconds()/3600
    return max(0,duration-float(value.get('excluded_minutes',0))/60)


def daytime_hours(value,process_code):
    period=bounds(value | {'working':True})
    hours=(period[1]-period[0]).total_seconds()/3600
    if process_code=='TP' and hours==24 and value.get('excluded_minutes')==210:
        return 620/60
    return min(12,hours)*net_hours(value)/hours


def standard_daytime_hours(process_code):
    return 620/60 if process_code=='TP' else 12


def workday_segments(db,process_code,first,last,now,equipment_id=None):
    # Shift boundaries remain physical; planned exclusions are spread over elapsed time.
    rows=days(db,process_code,first,last,equipment_id)
    result=[]
    for value in rows:
        period=bounds(value)
        if period:
            a,b=period[0],min(period[1],now)
            duration=(period[1]-period[0]).total_seconds()
            weight=max(0,1-float(value.get('excluded_minutes',0))*60/duration)
            if b>a:result.append((a,b,weight))
    return result,[r for r in rows if r['holiday']]


def weighted_seconds(segments):
    """Union of overlapping shifts, counting each instant once at its highest available weight."""
    events=defaultdict(list)
    for a,b,weight in segments:events[a].append((weight,1));events[b].append((weight,-1))
    active=Counter();previous=None;total=0
    for instant in sorted(events):
        if previous is not None and active:total+=(instant-previous).total_seconds()*max(active)
        for weight,delta in events[instant]:
            active[weight]+=delta
            if active[weight]<=0:del active[weight]
        previous=instant
    return total


def workday_intervals(db,process_code,first,last,now,equipment_id=None):
    segments,holidays=workday_segments(db,process_code,first,last,now,equipment_id)
    return [(a,b) for a,b,_ in segments],holidays
