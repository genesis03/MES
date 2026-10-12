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
from services.oee_schedule import workday_intervals


def lathe_codes(db):
    return [p.process_code for p in db.query(ProcessModel) if stage_suffix(p.process_name)=='-A']


def merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return merged


def calculate(db,start_day,end_day,now=None,process_code=None):
    now=now or datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    start=datetime.combine(start_day,time());end=min(datetime.combine(end_day+timedelta(days=1),time()),now)
    codes=[process_code] if process_code else lathe_codes(db)
    intervals,excluded=workday_intervals(db,codes[0],start_day,end_day,now) if codes else ([],[])
    intervals=merge_intervals(intervals)
    planned=sum((b-a).total_seconds() for a,b in intervals)
    if intervals:start,end=intervals[0][0],intervals[-1][1]
    equipment={e.id:e for e in db.query(EquipmentMaster).filter(EquipmentMaster.process_code.in_(codes))}
    machines={}
    for e in equipment.values():machines.setdefault(e.machine_no,[]).append(e)
    groups={}
    def key_for(equipment_id=None,machine_no='',name=''):
        if equipment_id in equipment:return str(equipment_id)
        matches=machines.get(machine_no,[])
        if len(matches)==1:return str(matches[0].id)
        return 'unknown:'+str(machine_no or name or '미확인')
    def group(key):
        if key not in groups:
            e=equipment.get(int(key)) if key.isdigit() else None
            periods=merge_intervals(workday_intervals(db,e.process_code,start_day,end_day,now,e.id)[0]) if e else intervals
            groups[key]=dict(intervals=periods,key=key,equipment_name=e.equipment_name if e else key.split(':',1)[1]+' (호기 연결 미확인)',planned_seconds=sum((b-a).total_seconds() for a,b in periods),good_qty=0,total_qty=0,ideal_seconds=0,downtime_intervals=[],issues=[],parts={},missing=set())
        return groups[key]
    for e in equipment.values():
        if e.is_active=='Y':group(str(e.id))
    cycles={(line.equipment_id,line.item_id,day.work_date):day.cycle_seconds for day,line in db.query(ProductionMonthPlanDay,ProductionMonthPlanLine).join(ProductionMonthPlanLine,ProductionMonthPlanLine.id==ProductionMonthPlanDay.line_id).filter(ProductionMonthPlanDay.work_date>=start_day.isoformat(),ProductionMonthPlanDay.work_date<=end_day.isoformat())}
    items={i.id:i for i in db.query(ItemMasterModel)}
    def add(key,item_id,part_no,total,good,source,work_date):
        g=group(key);g['good_qty']+=float(good or 0)
        cycle=cycles.get((int(key),item_id,work_date)) if key.isdigit() else None
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
        add(key_for(perf.equipment_id,machine_no,perf.equipment_name),order.item_id,item.part_no if item else order.part_no,float(perf.good_qty or 0)+float(perf.defect_qty or 0)+float(perf.setup_qty or 0),perf.good_qty,'내부',perf.performance_date)
    connections=ItemConnections(db)
    for record in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.work_date>=start_day.isoformat(),ExternalProductionRecord.work_date<=min(end_day,now.date()).isoformat()):
        view=external_record_view(record,connections)
        if view['lot_conflict'] or view['process_code'] not in codes:continue
        add(key_for(machine_no=view['machine_no']),view['item_id'],view['mes_part_no'] or record.part_no,view['total_qty'],record.job_qty,'외부',record.work_date)
    all_periods=[p for g in groups.values() for p in g['intervals']]
    if all_periods:start,end=min(p[0] for p in all_periods),max(p[1] for p in all_periods)
    for downtime,run in db.query(ProductionRunDowntime,ProductionRun).join(ProductionRun,ProductionRun.id==ProductionRunDowntime.run_id).filter(ProductionRun.process_code.in_(codes),ProductionRun.status!='CANCELLED',ProductionRunDowntime.ended_at>start.strftime('%Y-%m-%d %H:%M'),ProductionRunDowntime.started_at<end.strftime('%Y-%m-%d %H:%M')):
        group(key_for(run.equipment_id,name=run.equipment_name))['downtime_intervals'].append((parse_time(downtime.started_at),parse_time(downtime.ended_at)))
    result=[]
    for key,g in groups.items():
        intervals=g.pop('intervals');planned=g['planned_seconds']
        downtime=sum(max(0,(min(b,d)-max(a,c)).total_seconds()) for a,b in merge_intervals(g.pop('downtime_intervals')) for c,d in intervals)
        runtime=max(0,planned-downtime);missing=sorted(g.pop('missing'))
        g['issues'].extend('생산계획 C/T 미등록: '+part for part in missing)
        if not planned and g['total_qty']:g['issues'].append('가동 스케줄에 경과 시간이 없는데 생산실적이 있습니다. 가동일·시간을 확인하세요.')
        if key.startswith('unknown:'):g['issues'].append('생산호기를 설비 정보와 연결할 수 없습니다.')
        availability=runtime/planned if planned else None
        performance=g['ideal_seconds']/runtime if runtime and not g['issues'] else (0 if planned and not g['total_qty'] and not g['issues'] else None)
        quality=g['good_qty']/g['total_qty'] if g['total_qty'] and not any('총생산수량' in x for x in g['issues']) else None
        oee=availability*performance*quality if None not in (availability,performance,quality) else (0 if planned and not g['total_qty'] and not g['issues'] else None)
        g.update(downtime_seconds=downtime,runtime_seconds=runtime,availability=availability,performance=performance,quality=quality,oee=oee)
        if performance is not None and performance>1:g['issues'].append('성능률 100% 초과: 사이클타임·실적·비가동 기록 확인')
        g['parts']=list(g['parts'].values())
        for p in g['parts']:p['sources']=sorted(p['sources'])
        result.append(g)
    return dict(items=sorted(result,key=lambda x:x['equipment_name']),holidays=[dict(date=r['work_date'],name=r['holiday'],working=r['working']) for r in excluded],as_of=now.isoformat(timespec='minutes'),planned_seconds=sum((b-a).total_seconds() for a,b in merge_intervals(all_periods)),process_code=process_code or (codes[0] if codes else ''))
