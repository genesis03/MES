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


def shift_runtime(shifts, unknown, tapping):
    known=set(shifts)
    for shift in ('DAY','NIGHT'):
        if unknown and shift not in known:known.add(shift);unknown-=1
    return sum((620 if shift=='DAY' else 610)*60 if tapping else 12*3600 for shift in known)


def merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return merged


def calculate(db,start_day,end_day,now=None,process_code=None,exclude_unplanned=False):
    now=now or datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    start=datetime.combine(start_day,time());end=min(datetime.combine(end_day+timedelta(days=1),time()),now)
    processes={p.process_code:p for p in db.query(ProcessModel)}
    allowed=managed_codes(db)
    codes=[process_code] if process_code in allowed else allowed if not process_code else []
    excluded={}
    equipment={e.id:e for e in db.query(EquipmentMaster).filter(EquipmentMaster.process_code.in_(codes))}
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
            segments,holidays=workday_segments(db,e.process_code,start_day,end_day,now,e.id) if e else ([],[])
            excluded.update({r['work_date']:r for r in holidays})
            periods=merge_intervals([(a,b) for a,b,_ in segments])
            groups[key]=dict(segments=segments,intervals=periods,shift_days={},process_code=e.process_code if e else key.split(':')[1],planned_stop_seconds=weighted_seconds([(a,b,1-weight) for a,b,weight in segments]),key=key,equipment_name=e.equipment_name if e else key.split(':',1)[1]+' (호기 연결 미확인)',planned_seconds=weighted_seconds(segments),good_qty=0,total_qty=0,ideal_seconds=0,downtime_intervals=[],issues=[],parts={},missing=set(),excluded_records=0,excluded_total_qty=0,excluded_good_qty=0,excluded_parts=set())
        return groups[key]
    for e in equipment.values():
        if e.is_active=='Y':group(str(e.id))
    cycles={(line.equipment_id,line.item_id,day.work_date):day.cycle_seconds for day,line in db.query(ProductionMonthPlanDay,ProductionMonthPlanLine).join(ProductionMonthPlanLine,ProductionMonthPlanLine.id==ProductionMonthPlanDay.line_id).filter(ProductionMonthPlanDay.work_date>=start_day.isoformat(),ProductionMonthPlanDay.work_date<=end_day.isoformat())}
    items={i.id:i for i in db.query(ItemMasterModel)}
    def add(key,item_id,part_no,total,good,source,work_date,shift=None):
        g=group(key)
        cycle=cycles.get((int(key),item_id,work_date)) if key.isdigit() else None
        if exclude_unplanned and cycle is None:
            g['excluded_records']+=1
            g['excluded_total_qty']+=float(total or 0)
            g['excluded_good_qty']+=float(good or 0)
            g['excluded_parts'].add(part_no)
            return
        g['good_qty']+=float(good or 0)
        day=g['shift_days'].setdefault(work_date,dict(shifts=set(),unknown=0))
        if shift in ('DAY','NIGHT'):day['shifts'].add(shift)
        else:day['unknown']+=1
        p=g['parts'].setdefault((part_no,cycle),dict(part_no=part_no,total_qty=0,good_qty=0,cycle_seconds=cycle,sources=set()))
        p['good_qty']+=float(good or 0);p['sources'].add(source)
        if total is None:g['issues'].append(part_no+': 총생산수량 미확인');return
        g['total_qty']+=float(total);p['total_qty']+=float(total)
        if cycle is None:g['missing'].add(part_no)
        else:g['ideal_seconds']+=float(total)*cycle
    for perf,order in db.query(ProductionPerformance,ProductionWorkOrder).join(ProductionWorkOrder,ProductionWorkOrder.id==ProductionPerformance.work_order_id).filter(ProductionPerformance.process_code.in_(codes),ProductionPerformance.performance_date>=start_day.isoformat(),ProductionPerformance.performance_date<=min(end_day,now.date()).isoformat()):
        item=items.get(order.item_id)
        e=equipment.get(perf.equipment_id)
        machine_no=e.machine_no if e else ''
        if not machine_no:
            match=re.search(r'(\d+)\s*호기',perf.equipment_name or '')
            machine_no=match.group(1) if match else ''
        add(key_for(perf.equipment_id,machine_no,perf.equipment_name,perf.process_code),order.item_id,item.part_no if item else order.part_no,float(perf.good_qty or 0)+float(perf.defect_qty or 0)+float(perf.setup_qty or 0),perf.good_qty,'내부',perf.performance_date,perf.shift_type)
    connections=ItemConnections(db)
    for record in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.work_date>=start_day.isoformat(),ExternalProductionRecord.work_date<=min(end_day,now.date()).isoformat()):
        view=external_record_view(record,connections)
        if view['lot_conflict'] or view['process_code'] not in codes:continue
        add(key_for(machine_no=view['machine_no'],code=view['process_code']),view['item_id'],view['mes_part_no'] or record.part_no,view['total_qty'],record.job_qty,'외부',record.work_date)
        for stop in view['downtimes']:
            group(key_for(machine_no=view['machine_no'],code=view['process_code']))['downtime_intervals'].append((parse_time(stop['started_at']),parse_time(stop['ended_at'])))
    all_periods=[p for g in groups.values() for p in g['intervals']]
    if all_periods:start,end=min(p[0] for p in all_periods),max(p[1] for p in all_periods)
    for downtime,run in db.query(ProductionRunDowntime,ProductionRun).join(ProductionRun,ProductionRun.id==ProductionRunDowntime.run_id).filter(ProductionRun.process_code.in_(codes),ProductionRun.status!='CANCELLED',ProductionRunDowntime.ended_at>start.strftime('%Y-%m-%d %H:%M'),ProductionRunDowntime.started_at<end.strftime('%Y-%m-%d %H:%M')):
        group(key_for(run.equipment_id,name=run.equipment_name,code=run.process_code))['downtime_intervals'].append((parse_time(downtime.started_at),parse_time(downtime.ended_at)))
    result=[]
    for key,g in groups.items():
        intervals=g.pop('intervals');g.pop('segments');planned=g['planned_seconds']
        registered_downtime=sum(max(0,(min(b,d)-max(a,c)).total_seconds()) for a,b in merge_intervals(g.pop('downtime_intervals')) for c,d in intervals)
        tapping=stage_suffix(processes[g['process_code']].process_name)=='-B'
        runtime=sum(shift_runtime(day['shifts'],day['unknown'],tapping) for day in g.pop('shift_days').values())
        downtime=max(0,planned-runtime);missing=sorted(g.pop('missing'))
        if runtime>planned:g['issues'].append('실적 기준 가동시간이 기본시간을 초과합니다. 생산계획·실적 교대를 확인하세요.')
        if planned and not runtime:g['issues'].append('생산실적 없음: 기본시간 전체를 비가동으로 계산합니다.')
        g['issues'].extend('생산계획 C/T 미등록: '+part for part in missing)
        if not planned and g['total_qty']:g['issues'].append('가동 스케줄에 경과 시간이 없는데 생산실적이 있습니다. 가동일·시간을 확인하세요.')
        if key.startswith('unknown:'):g['issues'].append('생산호기를 설비 정보와 연결할 수 없습니다.')
        availability=runtime/planned if planned else None
        performance=g['ideal_seconds']/runtime if runtime and not missing and not any('총생산수량' in x for x in g['issues']) else (0 if planned and not g['total_qty'] else None)
        quality=g['good_qty']/g['total_qty'] if g['total_qty'] and not any('총생산수량' in x for x in g['issues']) else None
        oee=availability*performance*quality if None not in (availability,performance,quality) else (0 if planned and not g['total_qty'] else None)
        g.update(registered_downtime_seconds=registered_downtime,downtime_seconds=downtime,runtime_seconds=runtime,availability=availability,performance=performance,quality=quality,oee=oee)
        if performance is not None and performance>1:g['issues'].append('성능률 100% 초과: 사이클타임·실적·비가동 기록 확인')
        g['excluded_parts']=sorted(g['excluded_parts'])
        g['parts']=list(g['parts'].values())
        for p in g['parts']:p['sources']=sorted(p['sources'])
        result.append(g)
    summary={field:sum(row[field] for row in result) for field in (
        'planned_seconds','planned_stop_seconds','downtime_seconds','runtime_seconds',
        'total_qty','good_qty','ideal_seconds','excluded_records','excluded_total_qty','excluded_good_qty')}
    planned,runtime,total=summary['planned_seconds'],summary['runtime_seconds'],summary['total_qty']
    incomplete=any(row['performance'] is None and row['parts'] for row in result)
    unconfirmed=any('총생산수량' in issue for row in result for issue in row['issues'])
    availability=runtime/planned if planned else None
    performance=summary['ideal_seconds']/runtime if runtime and not incomplete else (0 if planned and not total else None)
    quality=summary['good_qty']/total if total and not unconfirmed else None
    oee=availability*performance*quality if None not in (availability,performance,quality) else (0 if planned and not total and not unconfirmed else None)
    summary.update(name=processes[process_code].process_name+' 종합' if process_code in processes else '전체 종합 (복합선반·탭핑)',availability=availability,performance=performance,quality=quality,oee=oee)
    return dict(summary=summary,exclude_unplanned=exclude_unplanned,items=sorted(result,key=lambda x:x['equipment_name']),holidays=[dict(date=r['work_date'],name=r['holiday'],working=r['working']) for r in sorted(excluded.values(),key=lambda r:r['work_date'])],as_of=now.isoformat(timespec='minutes'),planned_seconds=sum(g['planned_seconds'] for g in result),process_code=process_code or '')
