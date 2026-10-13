"""Read-only annual and daily reports using the equipment OEE calculation."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from models.equipment import EquipmentMaster
from models.models import ItemMasterModel, ProcessModel
from models.production_month_plan import ProductionMonthPlanDay, ProductionMonthPlanLine
from services.equipment_oee import calculate, managed_codes, summarize
from services.oee_schedule import month_days


def report_now():
    return datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)


def metrics(row):
    result=dict(row)
    result['attainment']=row['good_qty']/row['planned_qty'] if row['planned_qty'] else None
    result['defect_rate']=row['defect_qty']/row['total_qty'] if row['total_qty'] and row['quality'] is not None else None
    complete=row['standard_time_complete']
    result['standard_production_seconds']=row['ideal_seconds'] if complete else None
    result['standard_good_seconds']=row['ideal_good_seconds'] if complete else None
    result['quality_loss_seconds']=row['ideal_defect_seconds']+row['ideal_setup_seconds'] if complete else None
    return result


def efficiency_report(db,year,month,process_code=None,exclude_unplanned=False,now=None):
    now=now or report_now()
    months=[];selected=None
    for number in range(1,13):
        dates=month_days(f'{year:04d}-{number:02d}')
        data=calculate(db,dates[0],dates[-1],now=now,process_code=process_code,exclude_unplanned=exclude_unplanned)
        months.append(dict(month=number,summary=metrics(data['summary'])))
        if number==month:selected=data
    annual=calculate(db,date(year,1,1),date(year,12,31),now=now,process_code=process_code,exclude_unplanned=exclude_unplanned)
    equipment={e.id:e for e in db.query(EquipmentMaster)}
    names={p.process_code:p.process_name for p in db.query(ProcessModel)}
    rows=[]
    for row in selected['items']:
        value=metrics(row)
        e=equipment.get(int(row['key'])) if row['key'].isdigit() else None
        value.update(equipment_id=e.id if e else None,equipment_code=e.equipment_code if e else '',process_name=names.get(row['process_code'],''))
        rows.append(value)
    subtotals=[]
    for code in managed_codes(db):
        subset=[r for r in selected['items'] if r['process_code']==code]
        if subset:subtotals.append(dict(process_code=code,**metrics(summarize(subset,names[code]+' 소계'))))
    return dict(year=year,month=month,process_code=process_code or '',exclude_unplanned=exclude_unplanned,
                annual=metrics(annual['summary']),months=months,summary=metrics(selected['summary']),equipment=rows,process_summaries=subtotals)


def daily_report(db,month,equipment_id,exclude_unplanned=False,now=None):
    now=now or report_now();dates=month_days(month)
    e=db.get(EquipmentMaster,equipment_id)
    period=calculate(db,dates[0],dates[-1],now=now,process_code=e.process_code,exclude_unplanned=exclude_unplanned,equipment_id=e.id)
    plan_rows=db.query(ProductionMonthPlanDay,ProductionMonthPlanLine,ItemMasterModel).join(
        ProductionMonthPlanLine,ProductionMonthPlanLine.id==ProductionMonthPlanDay.line_id).join(
        ItemMasterModel,ItemMasterModel.id==ProductionMonthPlanLine.item_id).filter(
        ProductionMonthPlanLine.equipment_id==e.id,ProductionMonthPlanDay.work_date>=dates[0].isoformat(),
        ProductionMonthPlanDay.work_date<=dates[-1].isoformat(),ProductionMonthPlanDay.quantity>0).all()
    plans={}
    for day,_,item in plan_rows:plans.setdefault(day.work_date,[]).append(dict(part_no=item.part_no,cycle_seconds=day.cycle_seconds))
    def cycles(row,plan_entries):
        values={(p['part_no'],p['cycle_seconds']) for p in row['parts']}
        values.update((p['part_no'],p['cycle_seconds']) for p in plan_entries)
        return [dict(part_no=part,cycle_seconds=ct) for part,ct in sorted(values,key=lambda p:(p[0],p[1] or 0))]
    values=[]
    for day in dates:
        row=calculate(db,day,day,now=now,process_code=e.process_code,exclude_unplanned=exclude_unplanned,equipment_id=e.id)['items'][0]
        value=metrics(row)
        value.update(work_date=day.isoformat(),weekday=day.weekday(),cycles=cycles(row,plans.get(day.isoformat(),[])),
                     has_activity=bool(row['planned_qty'] or row['total_qty'] or row['performance_shift_seconds'] or row['planned_stop_seconds'] or row['downtime_seconds']))
        values.append(value)
    total=metrics(period['items'][0])
    total['cycles']=cycles(period['items'][0],[p for entries in plans.values() for p in entries])
    return dict(month=month,equipment_id=e.id,equipment_name=e.equipment_name,equipment_code=e.equipment_code,
                process_code=e.process_code,exclude_unplanned=exclude_unplanned,total=total,days=values)
