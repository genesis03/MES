from datetime import date,datetime,time,timedelta
import calendar
import holidays
from fastapi import HTTPException
from models.oee_schedule import OeeScheduleDay
from models.production_month_plan import ProductionEquipmentSchedule


def month_days(month):
    try:
        first=date.fromisoformat(month+'-01')
        if first.strftime('%Y-%m')!=month:raise ValueError()
    except ValueError:raise HTTPException(422,'올바른 조회 월을 선택하세요.')
    return [first+timedelta(days=i) for i in range(calendar.monthrange(first.year,first.month)[1])]


def default_day(day,public_days):
    return dict(work_date=day.isoformat(),working=day.weekday()<5 and day not in public_days,
                start_time='08:00',end_time='05:00' if day.weekday()==4 else '08:00',next_day=True,
                holiday=public_days.get(day,''),custom=False)


def days(db,process_code,first,last,equipment_id=None):
    public_days=holidays.KR(years=range(first.year,last.year+1),language='ko')
    overrides={r.work_date:r for r in db.query(OeeScheduleDay).filter(OeeScheduleDay.process_code==process_code,OeeScheduleDay.work_date>=first.isoformat(),OeeScheduleDay.work_date<=last.isoformat())}
    if equipment_id is not None:
        overrides.update({r.work_date:r for r in db.query(ProductionEquipmentSchedule).filter(ProductionEquipmentSchedule.equipment_id==equipment_id,ProductionEquipmentSchedule.work_date>=first.isoformat(),ProductionEquipmentSchedule.work_date<=last.isoformat())})
    result=[]
    while first<=last:
        value=default_day(first,public_days)
        row=overrides.get(first.isoformat())
        if row:value.update(working=row.working,start_time=row.start_time,end_time=row.end_time,next_day=row.next_day,custom=True)
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


def save_month(db,process_code,month,values,equipment_id=None):
    dates=month_days(month)
    if len(values)!=len(dates) or {v['work_date'] for v in values}!={d.isoformat() for d in dates}:
        raise HTTPException(422,'선택한 월의 날짜별 스케줄을 중복 없이 모두 입력하세요.')
    merged={r['work_date']:r for r in days(db,process_code,dates[0]-timedelta(days=1),dates[-1]+timedelta(days=1),equipment_id)}
    merged.update({r['work_date']:r for r in values})
    periods=sorted(p for value in merged.values() if (p:=bounds(value)))
    for previous,current in zip(periods,periods[1:]):
        if current[0]<previous[1]:raise HTTPException(422,'인접한 가동일의 시간이 겹칩니다: '+current[0].strftime('%Y-%m-%d %H:%M'))
    model=ProductionEquipmentSchedule if equipment_id is not None else OeeScheduleDay
    query=db.query(model).filter(model.work_date>=dates[0].isoformat(),model.work_date<=dates[-1].isoformat())
    query=query.filter(model.equipment_id==equipment_id) if equipment_id is not None else query.filter(model.process_code==process_code)
    existing={r.work_date:r for r in query}
    for value in values:
        row=existing.get(value['work_date'])
        if row is None:
            row=model(work_date=value['work_date'],**({'equipment_id':equipment_id} if equipment_id is not None else {'process_code':process_code}));db.add(row)
        for name in ['working','start_time','end_time','next_day']:setattr(row,name,value[name])
    db.commit()
