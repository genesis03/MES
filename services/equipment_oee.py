"""선택한 가공 공정의 월별 가동 스케줄과 생산실적을 집계합니다."""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from models.models import ProcessModel, ItemMasterModel
from models.equipment import EquipmentMaster
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_run import ProductionRun, ProductionRunDowntime
from models.production_month_plan import ProductionMonthPlanLine,ProductionMonthPlanDay
from models.production_sync import ExternalProductionRecord
from services.production_sync_mapping import ItemConnections, stage_suffix
from services.production_sync_view import external_record_view
from services.production_downtime import parse_time
from services.oee_schedule import workday_segments,weighted_seconds


def managed_codes(db):
    return [p.process_code for p in db.query(ProcessModel) if p.is_active=='Y' and stage_suffix(p.process_name) in ('-A','-B')]


def counted_shifts(shifts, unknown):
    known=set(shifts)
    for shift in ('DAY','NIGHT'):
        if unknown and shift not in known:known.add(shift);unknown-=1
    return known


def shift_runtime(shifts, unknown, tapping):
    return sum((620 if shift=='DAY' else 610)*60 if tapping else 12*3600
               for shift in counted_shifts(shifts, unknown))


def runtime_intervals(shift_days, now):
    """Stop deductions use the full shifts counted in the runtime baseline.

    Keep the existing unknown-count policy. For one unknown record, retain
    its physical night alignment when the source indicates a night operation.
    """
    intervals=[]
    for work_date, day in shift_days.items():
        shifts=counted_shifts(day['shifts'],day['unknown'])
        if not day['shifts'] and day['unknown']==1 and day['unknown_alignment']:
            shifts={day['unknown_alignment'][0]}
        for shift in shifts:
            intervals.append(performance_window(work_date,shift,None,None,now))
    return merge_intervals(intervals)


def merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return merged


def intersect_intervals(intervals,windows):
    return merge_intervals([(max(a,c),min(b,d)) for a,b in merge_intervals(intervals) for c,d in merge_intervals(windows) if max(a,c)<min(b,d)])


def subtract_intervals(intervals,excluded):
    result=[]
    for a,b in merge_intervals(intervals):
        for c,d in merge_intervals(excluded):
            if d<=a or c>=b:continue
            if c>a:result.append((a,c))
            a=max(a,d)
            if a>=b:break
        if a<b:result.append((a,b))
    return result


def seconds(intervals):
    return sum((b-a).total_seconds() for a,b in merge_intervals(intervals))


def is_planned_stop(code,name):
    name=re.sub(r'\s+','',name or '')
    if name:return name=='계획정지'
    return str(code or '').upper() in ('8','PLAN_STOP','PLANNED_STOP')


def performance_window(day,shift,start,end,now):
    def clock(value):
        value=str(value or '')
        return datetime.strptime(value,'%Y%m%d%H%M%S') if len(value)==14 and value.isdigit() else parse_time(value[:16])
    if start and end:
        a,b=clock(start),clock(end)
    else:
        a=datetime.combine(date.fromisoformat(day),time(8))
        if shift=='NIGHT':a+=timedelta(hours=12)
        b=a+timedelta(hours=12 if shift in ('DAY','NIGHT') else 24)
    return a,min(b,now)


def calculate(db,start_day,end_day,now=None,process_code=None,exclude_unplanned=False,equipment_id=None):
    now=now or datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    start=datetime.combine(start_day,time());end=min(datetime.combine(end_day+timedelta(days=1),time()),now)
    processes={p.process_code:p for p in db.query(ProcessModel)}
    allowed=managed_codes(db)
    codes=[process_code] if process_code in allowed else allowed if not process_code else []
    equipment_query=db.query(EquipmentMaster).filter(EquipmentMaster.process_code.in_(codes))
    if equipment_id is not None:equipment_query=equipment_query.filter(EquipmentMaster.id==equipment_id)
    equipment={e.id:e for e in equipment_query}
    plan_rows=db.query(ProductionMonthPlanDay,ProductionMonthPlanLine).join(ProductionMonthPlanLine,ProductionMonthPlanLine.id==ProductionMonthPlanDay.line_id).filter(ProductionMonthPlanDay.work_date>=start_day.isoformat(),ProductionMonthPlanDay.work_date<=end_day.isoformat()).all()
    plan_quantities={}
    for day,line in plan_rows:plan_quantities[line.equipment_id]=plan_quantities.get(line.equipment_id,0)+float(day.quantity)
    machines={}
    for e in equipment.values():machines.setdefault((e.process_code,e.machine_no),[]).append(e)
    groups={}
    def key_for(equipment_id=None,machine_no='',name='',code=''):
        if equipment_id in equipment:return str(equipment_id)
        matches=machines.get((code,machine_no),[])
        if len(matches)==1:return str(matches[0].id)
        return 'unknown:'+code+':'+str(machine_no or name or '미확인')
    def group(key):
        if key not in groups:
            e=equipment.get(int(key)) if key.isdigit() else None
            segments,_=workday_segments(db,e.process_code,start_day,end_day,now,e.id) if e else ([],[])
            periods=merge_intervals([(a,b) for a,b,_ in segments])
            groups[key]=dict(segments=segments,intervals=periods,shift_days={},process_code=e.process_code if e else key.split(':')[1],planned_stop_seconds=0,planned_break_seconds=weighted_seconds([(a,b,1-weight) for a,b,weight in segments]),key=key,equipment_name=e.equipment_name if e else key.split(':',1)[1]+' (호기 연결 미확인)',planned_seconds=weighted_seconds(segments),planned_qty=plan_quantities.get(e.id,0) if e else 0,good_qty=0,total_qty=0,defect_qty=0,setup_qty=0,ideal_seconds=0,ideal_good_seconds=0,ideal_defect_seconds=0,ideal_setup_seconds=0,downtime_intervals=[],planned_stop_intervals=[],observed_intervals=[],issues=[],parts={},missing=set(),excluded_records=0,excluded_total_qty=0,excluded_good_qty=0,excluded_parts=set())
        return groups[key]
    for e in equipment.values():
        if e.is_active=='Y' or e.id==equipment_id:group(str(e.id))
    cycles={(line.equipment_id,line.item_id,day.work_date):day.cycle_seconds for day,line in plan_rows}
    items={i.id:i for i in db.query(ItemMasterModel)}
    def add(key,item_id,part_no,total,good,source,work_date,shift=None,start_time=None,end_time=None,window_shift=None,defect=0,setup=0):
        if equipment_id is not None and key!=str(equipment_id):return False
        g=group(key)
        cycle=cycles.get((int(key),item_id,work_date)) if key.isdigit() else None
        if exclude_unplanned and cycle is None:
            g['excluded_records']+=1
            g['excluded_total_qty']+=float(total or 0)
            g['excluded_good_qty']+=float(good or 0)
            g['excluded_parts'].add(part_no)
            return False
        observed=performance_window(work_date,window_shift or shift,start_time,end_time,now)
        g['observed_intervals'].append(observed)
        g['good_qty']+=float(good or 0)
        g['defect_qty']+=float(defect or 0);g['setup_qty']+=float(setup or 0)
        day=g['shift_days'].setdefault(work_date,dict(shifts=set(),unknown=0,unknown_alignment=[]))
        if shift in ('DAY','NIGHT'):day['shifts'].add(shift)
        else:
            day['unknown']+=1
            alignment={'DAY_SHIFT':'DAY','NIGHT_SHIFT':'NIGHT','DAY':'DAY','NIGHT':'NIGHT'}.get(window_shift)
            if not alignment:
                night_start=datetime.combine(date.fromisoformat(work_date),time(20))
                alignment='NIGHT' if observed[0]>=night_start else 'DAY'
            day['unknown_alignment'].append(alignment)
        p=g['parts'].setdefault((part_no,cycle),dict(part_no=part_no,total_qty=0,good_qty=0,cycle_seconds=cycle,sources=set()))
        p['good_qty']+=float(good or 0);p['sources'].add(source)
        if total is None:g['issues'].append(part_no+': 총생산수량 미확인');return True
        g['total_qty']+=float(total);p['total_qty']+=float(total)
        if cycle is None:g['missing'].add(part_no)
        else:
            g['ideal_seconds']+=float(total)*cycle
            g['ideal_good_seconds']+=float(good or 0)*cycle
            g['ideal_defect_seconds']+=float(defect or 0)*cycle
            g['ideal_setup_seconds']+=float(setup or 0)*cycle
        return True
    run_times={r.performance_id:r for r in db.query(ProductionRun).filter(ProductionRun.performance_id.isnot(None),ProductionRun.status!='CANCELLED',ProductionRun.performance_date>=start_day.isoformat(),ProductionRun.performance_date<=min(end_day,now.date()).isoformat())}
    for perf,order in db.query(ProductionPerformance,ProductionWorkOrder).join(ProductionWorkOrder,ProductionWorkOrder.id==ProductionPerformance.work_order_id).filter(ProductionPerformance.process_code.in_(codes),ProductionPerformance.performance_date>=start_day.isoformat(),ProductionPerformance.performance_date<=min(end_day,now.date()).isoformat()):
        item=items.get(order.item_id)
        e=equipment.get(perf.equipment_id)
        machine_no=e.machine_no if e else ''
        if not machine_no:
            match=re.search(r'(\d+)\s*호기',perf.equipment_name or '')
            machine_no=match.group(1) if match else ''
        run=run_times.get(perf.id)
        add(key_for(perf.equipment_id,machine_no,perf.equipment_name,perf.process_code),order.item_id,item.part_no if item else order.part_no,float(perf.good_qty or 0)+float(perf.defect_qty or 0)+float(perf.setup_qty or 0),perf.good_qty,'내부',perf.performance_date,perf.shift_type,run.start_time if run else None,run.end_time if run else None,defect=perf.defect_qty,setup=perf.setup_qty)
    connections=ItemConnections(db)
    for record in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.work_date>=start_day.isoformat(),ExternalProductionRecord.work_date<=min(end_day,now.date()).isoformat()):
        view=external_record_view(record,connections)
        if view['lot_conflict'] or view['process_code'] not in codes:continue
        included=add(key_for(machine_no=view['machine_no'],code=view['process_code']),view['item_id'],view['mes_part_no'] or record.part_no,view['total_qty'],record.job_qty,'외부',record.work_date,start_time=record.started_at,end_time=record.ended_at,window_shift=view['shift_type'],defect=record.fault_qty,setup=view['setup_qty'])
        if included:
            for stop in view['downtimes']:
                field='planned_stop_intervals' if is_planned_stop(stop['type_code'],stop['type_name']) else 'downtime_intervals'
                group(key_for(machine_no=view['machine_no'],code=view['process_code']))[field].append((parse_time(stop['started_at']),parse_time(stop['ended_at'])))
    for g in groups.values():g['runtime_intervals']=runtime_intervals(g['shift_days'],now)
    all_periods=[p for g in groups.values() for p in g['intervals']+g['observed_intervals']+g['runtime_intervals'] if p[1]>p[0]]
    if all_periods:start,end=min(p[0] for p in all_periods),max(p[1] for p in all_periods)
    for downtime,run in db.query(ProductionRunDowntime,ProductionRun).join(ProductionRun,ProductionRun.id==ProductionRunDowntime.run_id).filter(ProductionRun.process_code.in_(codes),ProductionRun.status!='CANCELLED',ProductionRun.performance_date>=start_day.isoformat(),ProductionRun.performance_date<=min(end_day,now.date()).isoformat(),ProductionRunDowntime.ended_at>start.strftime('%Y-%m-%d %H:%M'),ProductionRunDowntime.started_at<end.strftime('%Y-%m-%d %H:%M')):
        order=db.get(ProductionWorkOrder,run.work_order_id)
        if exclude_unplanned and (not order or (run.equipment_id,order.item_id,run.performance_date) not in cycles):continue
        if equipment_id is not None and run.equipment_id!=equipment_id:continue
        field='planned_stop_intervals' if is_planned_stop(downtime.type_code,downtime.type_name) else 'downtime_intervals'
        group(key_for(run.equipment_id,name=run.equipment_name,code=run.process_code))[field].append((parse_time(downtime.started_at),parse_time(downtime.ended_at)))
    result=[]
    for key,g in groups.items():
        intervals=g.pop('intervals');g.pop('segments');schedule_seconds=g['planned_seconds']
        g['schedule_seconds']=schedule_seconds
        observed=merge_intervals(g.pop('observed_intervals'))
        runtime_windows=g.pop('runtime_intervals',[])
        windows=merge_intervals(intervals+observed+runtime_windows)
        plan_stops=intersect_intervals(g.pop('planned_stop_intervals'),windows)
        other_stops=subtract_intervals(intersect_intervals(g.pop('downtime_intervals'),windows),plan_stops)
        g['planned_stop_seconds']=seconds(plan_stops)
        planned=max(0,schedule_seconds-seconds(intersect_intervals(plan_stops,intervals)))
        g['planned_seconds']=planned
        downtime=seconds(other_stops)
        registered_downtime=g['planned_stop_seconds']+downtime
        tapping=stage_suffix(processes[g['process_code']].process_name)=='-B'
        baseline=sum(shift_runtime(day['shifts'],day['unknown'],tapping) for day in g.pop('shift_days').values())
        actual_stops=seconds(intersect_intervals(plan_stops+other_stops,runtime_windows))
        runtime=max(0,baseline-actual_stops);missing=sorted(g.pop('missing'))
        g['performance_shift_seconds']=baseline
        if actual_stops>baseline:g['issues'].append('등록된 정지시간이 실적 건수로 계산한 시간을 초과합니다. 정지 내역을 확인하세요.')
        if runtime>planned:g['issues'].append('실적 기준 가동시간이 기본시간을 초과합니다. 생산계획과 실적의 일자·주야 구분을 확인하세요.')
        g['issues'].extend('생산계획 C/T 미등록: '+part for part in missing)
        if not planned and g['total_qty']:g['issues'].append('가동 스케줄에 경과 시간이 없는데 생산실적이 있습니다. 가동일·시간을 확인하세요.')
        if key.startswith('unknown:'):g['issues'].append('생산호기를 설비 정보와 연결할 수 없습니다.')
        availability=runtime/planned if planned else None
        performance=g['ideal_seconds']/runtime if runtime and not missing and not any('총생산수량' in x for x in g['issues']) else (0 if planned and not g['total_qty'] else None)
        quality=g['good_qty']/g['total_qty'] if g['total_qty'] and not any('총생산수량' in x for x in g['issues']) else None
        oee=availability*performance*quality if None not in (availability,performance,quality) else (0 if planned and not g['total_qty'] else None)
        g.update(registered_downtime_seconds=registered_downtime,downtime_seconds=downtime,runtime_seconds=runtime,availability=availability,performance=performance,quality=quality,oee=oee,standard_time_complete=not missing and not any('총생산수량' in issue for issue in g['issues']))
        if performance is not None and performance>1:g['issues'].append('성능률 100% 초과: 사이클타임·실적·비가동 기록 확인')
        g['excluded_parts']=sorted(g['excluded_parts'])
        g['parts']=list(g['parts'].values())
        for p in g['parts']:p['sources']=sorted(p['sources'])
        result.append(g)
    name=processes[process_code].process_name+' 종합' if process_code in processes else '전체 종합 (복합선반·탭핑)'
    summary=summarize(result,name)
    return dict(summary=summary,exclude_unplanned=exclude_unplanned,items=sorted(result,key=lambda x:x['equipment_name']),planned_seconds=sum(g['planned_seconds'] for g in result),process_code=process_code or '')


def summarize(rows,name):
    summary={field:sum(row[field] for row in rows) for field in (
        'schedule_seconds','planned_seconds','planned_stop_seconds','planned_break_seconds','performance_shift_seconds','downtime_seconds','runtime_seconds',
        'planned_qty','total_qty','good_qty','defect_qty','setup_qty','ideal_seconds','ideal_good_seconds','ideal_defect_seconds','ideal_setup_seconds','excluded_records','excluded_total_qty','excluded_good_qty')}
    planned,runtime,total=summary['planned_seconds'],summary['runtime_seconds'],summary['total_qty']
    incomplete=any(row['performance'] is None and row['parts'] for row in rows)
    unconfirmed=any('총생산수량' in issue for row in rows for issue in row['issues'])
    availability=runtime/planned if planned else None
    performance=summary['ideal_seconds']/runtime if runtime and not incomplete else (0 if planned and not total else None)
    quality=summary['good_qty']/total if total and not unconfirmed else None
    oee=availability*performance*quality if None not in (availability,performance,quality) else (0 if planned and not total and not unconfirmed else None)
    summary.update(name=name,availability=availability,performance=performance,quality=quality,oee=oee,standard_time_complete=all(row['standard_time_complete'] for row in rows))
    return summary
