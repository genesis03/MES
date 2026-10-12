"""복합선반: 주간 달력 가동시간에서 등록 비가동을 차감하는 OEE."""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import holidays
from models.models import ProcessModel, ItemMasterModel
from models.equipment import EquipmentMaster
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_run import ProductionRun, ProductionRunDowntime
from models.production_cycle_time import ProductionCycleTime
from models.production_sync import ExternalProductionRecord
from services.production_sync_mapping import ItemConnections, stage_suffix
from services.production_sync_view import external_record_view
from services.production_downtime import parse_time


def lathe_codes(db):
    return [p.process_code for p in db.query(ProcessModel) if stage_suffix(p.process_name)=='-A']


def merge_intervals(intervals):
    merged=[]
    for start,end in sorted(intervals):
        if end<=start:continue
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    return merged


def scheduled_intervals(start,end):
    calendar=holidays.KR(years=range(start.year,end.year+1),language='ko')
    intervals=[];excluded=[];day=start.date()
    while day<=end.date():
        if day.weekday()<=5:
            left=datetime.combine(day,time(8) if day.weekday()==0 else time())
            right=datetime.combine(day,time(5)) if day.weekday()==5 else datetime.combine(day+timedelta(days=1),time())
            left,right=max(left,start),min(right,end)
            if right>left:
                if day in calendar:excluded.append({'date':day.isoformat(),'name':calendar[day]})
                else:intervals.append((left,right))
        day+=timedelta(days=1)
    return intervals,excluded


def calculate(db,start_day,end_day,now=None):
    now=now or datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
    start=datetime.combine(start_day,time());end=min(datetime.combine(end_day+timedelta(days=1),time()),now)
    intervals,excluded=scheduled_intervals(start,end) if end>start else ([],[])
    planned=sum((b-a).total_seconds() for a,b in intervals)
    codes=lathe_codes(db)
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
            groups[key]=dict(key=key,equipment_name=e.equipment_name if e else key.split(':',1)[1]+' (호기 연결 미확인)',planned_seconds=planned,good_qty=0,total_qty=0,ideal_seconds=0,downtime_intervals=[],issues=[],parts={},missing=set())
        return groups[key]
    for e in equipment.values():
        if e.is_active=='Y':group(str(e.id))
    cycles={c.item_id:c.seconds for c in db.query(ProductionCycleTime)}
    items={i.id:i for i in db.query(ItemMasterModel)}
    def add(key,item_id,part_no,total,good,source):
        g=group(key);g['good_qty']+=float(good or 0)
        p=g['parts'].setdefault(part_no,dict(part_no=part_no,total_qty=0,good_qty=0,cycle_seconds=cycles.get(item_id),sources=set()))
        p['good_qty']+=float(good or 0);p['sources'].add(source)
        if total is None:g['issues'].append(part_no+': 총생산수량 미확인');return
        g['total_qty']+=float(total);p['total_qty']+=float(total)
        if item_id not in cycles:g['missing'].add(part_no)
        else:g['ideal_seconds']+=float(total)*cycles[item_id]
    for perf,order in db.query(ProductionPerformance,ProductionWorkOrder).join(ProductionWorkOrder,ProductionWorkOrder.id==ProductionPerformance.work_order_id).filter(ProductionPerformance.process_code.in_(codes),ProductionPerformance.performance_date>=start_day.isoformat(),ProductionPerformance.performance_date<=min(end_day,now.date()).isoformat()):
        item=items.get(order.item_id)
        e=equipment.get(perf.equipment_id)
        machine_no=e.machine_no if e else ''
        if not machine_no:
            match=re.search(r'(\d+)\s*호기',perf.equipment_name or '')
            machine_no=match.group(1) if match else ''
        add(key_for(perf.equipment_id,machine_no,perf.equipment_name),order.item_id,item.part_no if item else order.part_no,float(perf.good_qty or 0)+float(perf.defect_qty or 0)+float(perf.setup_qty or 0),perf.good_qty,'내부')
    connections=ItemConnections(db)
    for record in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.work_date>=start_day.isoformat(),ExternalProductionRecord.work_date<=min(end_day,now.date()).isoformat()):
        view=external_record_view(record,connections)
        if view['lot_conflict'] or view['process_code'] not in codes:continue
        add(key_for(machine_no=view['machine_no']),view['item_id'],view['mes_part_no'] or record.part_no,view['total_qty'],record.job_qty,'외부')
    for downtime,run in db.query(ProductionRunDowntime,ProductionRun).join(ProductionRun,ProductionRun.id==ProductionRunDowntime.run_id).filter(ProductionRun.process_code.in_(codes),ProductionRun.status!='CANCELLED',ProductionRunDowntime.ended_at>start.strftime('%Y-%m-%d %H:%M'),ProductionRunDowntime.started_at<end.strftime('%Y-%m-%d %H:%M')):
        group(key_for(run.equipment_id,name=run.equipment_name))['downtime_intervals'].append((parse_time(downtime.started_at),parse_time(downtime.ended_at)))
    result=[]
    for key,g in groups.items():
        downtime=sum(max(0,(min(b,d)-max(a,c)).total_seconds()) for a,b in merge_intervals(g.pop('downtime_intervals')) for c,d in intervals)
        runtime=max(0,planned-downtime);missing=sorted(g.pop('missing'))
        g['issues'].extend('사이클타임 미등록: '+part for part in missing)
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
    return dict(items=sorted(result,key=lambda x:x['equipment_name']),holidays=excluded,as_of=now.isoformat(timespec='minutes'),planned_seconds=planned)
