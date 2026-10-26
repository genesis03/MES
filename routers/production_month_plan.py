import math
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,Field,field_validator
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel,ProcessModel,ItemBomModel
from models.equipment import EquipmentMaster
from models.production_cycle_time import ProductionCycleTime
from models.production_month_plan import ProductionMonthPlanLine,ProductionMonthPlanDay,ProductionPlanWorkTime
from routers.equipment_oee import require_access,machining_processes,validate_process,validate_equipment
from services.oee_schedule import month_days,days,bounds,default_excluded_minutes,net_hours,daytime_hours,standard_daytime_hours
from services.production_sync_mapping import stage_suffix
from services import production_plan_revisions as revisions
from types import SimpleNamespace

router=APIRouter(prefix='/api/production/month-plan',tags=['Monthly Production Plans'])


@router.get('/excel')
def excel(start_date:date,end_date:date,process_code:str|None=None,db:Session=Depends(get_db),user=Depends(get_current_user),revision:int|None=None):
    from services.production_plan_excel import build_plan_excel
    data=status(start_date,end_date,process_code,db,user,revision)
    return Response(build_plan_excel(data,start_date,end_date),
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':f'attachment; filename="ProductionPlan_{start_date}_{end_date}.xlsx"'})


def options_data(db):
    processes=machining_processes(db);codes={p.process_code for p in processes}
    equipment=db.query(EquipmentMaster).filter(EquipmentMaster.process_code.in_(codes),EquipmentMaster.is_active=='Y').order_by(EquipmentMaster.process_code,EquipmentMaster.sort_order,EquipmentMaster.machine_no).all()
    cycles={c.item_id:c.seconds for c in db.query(ProductionCycleTime)}
    linked={}
    for bom in db.query(ItemBomModel).filter(ItemBomModel.process_code.in_(codes)):linked.setdefault(bom.parent_item_id,set()).add(bom.process_code)
    items=[]
    for item in db.query(ItemMasterModel).filter(ItemMasterModel.is_active=='Y').order_by(ItemMasterModel.part_no):
        if item.material_type in ('RAW','원재료'):continue
        allowed=set(linked.get(item.id,set()))
        if item.production_loc in codes:allowed.add(item.production_loc)
        for process in processes:
            suffix=stage_suffix(process.process_name)
            if item.part_no.upper().endswith(suffix):allowed.add(process.process_code)
        if allowed:items.append(dict(id=item.id,part_no=item.part_no,part_name=item.part_name,process_codes=sorted(allowed),cycle_seconds=cycles.get(item.id)))
    return dict(processes=[dict(process_code=p.process_code,process_name=p.process_name) for p in processes],equipments=[dict(id=e.id,process_code=e.process_code,name=e.equipment_name,machine_no=e.machine_no) for e in equipment],items=items)


@router.get('/options')
def options(db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans');return options_data(db)


@router.get('')
def month(month:str,process_code:str,db:Session=Depends(get_db),user=Depends(get_current_user),revision:int|None=None):
    require_access(user,'/production/plans');validate_process(db,process_code);dates=month_days(month)
    equip_ids=[e.id for e in db.query(EquipmentMaster).filter(EquipmentMaster.process_code==process_code)]
    lines=db.query(ProductionMonthPlanLine).filter(ProductionMonthPlanLine.month==month,ProductionMonthPlanLine.equipment_id.in_(equip_ids)).order_by(ProductionMonthPlanLine.equipment_id,ProductionMonthPlanLine.id).all()
    names={i.id:i for i in db.query(ItemMasterModel)}
    equipment={e.id:e for e in db.query(EquipmentMaster)}
    meta=revisions.details(db,month)
    selected=revisions.snapshot(db,month,revision) if revision is not None else None
    if selected is not None:
        lines=[SimpleNamespace(id=None,**{k:v for k,v in r.items() if k!='days'},days=[SimpleNamespace(**d) for d in r['days']]) for r in selected['lines'] if r['equipment_id'] in equip_ids]
    try:require_access(user,'/production/plans','WRITE');can_write=True
    except HTTPException:can_write=False
    return dict(revision=meta,view_revision=revision,can_write=can_write,schedules={str(e):days(db,process_code,dates[0],dates[-1],e,applied=False,selected=selected) for e in equip_ids},month=month,dates=[d.isoformat() for d in dates],lines=[dict(id=r.id,equipment_id=r.equipment_id,item_id=r.item_id,part_no=names[r.item_id].part_no if r.item_id in names else '',part_name=names[r.item_id].part_name if r.item_id in names else '',equipment_name=equipment[r.equipment_id].equipment_name if r.equipment_id in equipment else '',cycle_seconds=r.cycle_seconds,expected_oee=r.expected_oee,quantities={d.work_date:d.quantity for d in r.days},cycle_history=sorted({d.cycle_seconds for d in r.days})) for r in lines])


class LineInput(BaseModel):
    equipment_id:int=Field(gt=0)
    item_id:int=Field(gt=0)
    cycle_seconds:float=Field(gt=0,le=86400)
    expected_oee:float=Field(gt=0,le=1)
    quantities:dict[str,float]
    @field_validator('cycle_seconds','expected_oee')
    @classmethod
    def finite(cls,value):
        if not math.isfinite(value):raise ValueError('유한한 숫자를 입력하세요.')
        return value
    @field_validator('quantities')
    @classmethod
    def quantities_valid(cls,value):
        if any(not math.isfinite(v) or v<0 for v in value.values()):raise ValueError('계획수량은 0 이상의 숫자로 입력하세요.')
        return value


class WorkTimeInput(BaseModel):
    equipment_id:int=Field(gt=0)
    work_date:date
    start_time:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end_time:str=Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    next_day:bool=True
    excluded_minutes:int|None=Field(default=None,ge=0,le=1439)


class MonthInput(BaseModel):
    month:str
    process_code:str
    ct_effective_date:date
    lines:list[LineInput]=Field(max_length=200)
    work_times:list[WorkTimeInput]=Field(default_factory=list,max_length=6200)
    deleted_line_ids:list[int]=Field(default_factory=list,max_length=200)
    generation:int|None=Field(default=None,ge=0)


class RevisionEditInput(MonthInput):
    generation:int=Field(ge=0)
    deleted_line_keys:list[tuple[int,int]]=Field(default_factory=list,max_length=200)
    reason:str=Field(default='',max_length=1000)


@router.put('/revisions/{number}')
def edit_revision(number:int,payload:RevisionEditInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans','WRITE');validate_process(db,payload.process_code)
    dates=month_days(payload.month);date_keys={d.isoformat() for d in dates}
    data=revisions.snapshot(db,payload.month,number);data.pop('_revision_number',None)
    equipment={e.id:e for e in db.query(EquipmentMaster)}
    scoped={(r['equipment_id'],r['item_id']):r for r in data['lines']
            if r['equipment_id'] in equipment and equipment[r['equipment_id']].process_code==payload.process_code}
    deleted=set(payload.deleted_line_keys)
    if payload.deleted_line_ids or len(deleted)!=len(payload.deleted_line_keys) or not deleted<=scoped.keys():
        raise HTTPException(422,'삭제할 행이 선택한 Rev·공정에 없습니다. 다시 불러오세요.')
    allowed={i['id'] for i in options_data(db)['items'] if payload.process_code in i['process_codes']}
    seen=set()
    for value in payload.lines:
        key=(value.equipment_id,value.item_id)
        if key in seen or key in deleted:raise HTTPException(422,'같은 호기·품번의 중복 또는 삭제 지정이 잘못되었습니다.')
        seen.add(key)
        if key not in scoped:
            validate_equipment(db,payload.process_code,value.equipment_id)
            if value.item_id not in allowed:raise HTTPException(422,'선택한 공정의 품번을 선택하세요.')
        if set(value.quantities)!=date_keys:raise HTTPException(422,'해당 월의 일별 계획수량을 모두 입력하세요.')
        row=scoped.get(key)
        if row and (row['cycle_seconds']!=value.cycle_seconds or row['expected_oee']!=value.expected_oee):
            if not dates[0]<=payload.ct_effective_date<=dates[-1]:raise HTTPException(422,'C/T·목표 OEE 변경 적용일은 선택한 월 안에서 지정하세요.')
    equipment_ids={key[0] for key in scoped}|{v.equipment_id for v in payload.lines}
    times={(r['equipment_id'],r['work_date']):r for r in data['times']}
    overrides={}
    for value in payload.work_times:
        key=(value.equipment_id,value.work_date.isoformat())
        if value.equipment_id not in equipment_ids or key[1] not in date_keys or key in overrides:
            raise HTTPException(422,'가동시간의 호기·날짜가 선택한 Rev·공정과 일치하지 않거나 중복되었습니다.')
        previous=times.get(key)
        excluded=value.excluded_minutes if value.excluded_minutes is not None else previous['excluded_minutes'] if previous else default_excluded_minutes(payload.process_code)
        period=bounds(dict(work_date=key[1],working=True,start_time=value.start_time,end_time=value.end_time,next_day=value.next_day))
        if excluded*60>=(period[1]-period[0]).total_seconds():raise HTTPException(422,'계획 차감시간은 시작·종료시간 사이보다 짧아야 합니다.')
        overrides[key]={'equipment_id':key[0],'work_date':key[1],'start_time':value.start_time,
                        'end_time':value.end_time,'next_day':value.next_day,'excluded_minutes':excluded}
    lines={(r['equipment_id'],r['item_id']):r for r in data['lines'] if (r['equipment_id'],r['item_id']) not in deleted}
    for value in payload.lines:
        key=(value.equipment_id,value.item_id);row=lines.get(key)
        new=row is None
        changed=new or row['cycle_seconds']!=value.cycle_seconds or row['expected_oee']!=value.expected_oee
        by_date={d['work_date']:d for d in row['days']} if row else {}
        for day in sorted(date_keys):
            saved=by_date.setdefault(day,{'work_date':day,'cycle_seconds':value.cycle_seconds,'expected_oee':value.expected_oee})
            if new or (changed and date.fromisoformat(day)>=payload.ct_effective_date):
                saved.update(cycle_seconds=value.cycle_seconds,expected_oee=value.expected_oee)
            saved['quantity']=value.quantities[day]
        lines[key]={'equipment_id':key[0],'item_id':key[1],'cycle_seconds':value.cycle_seconds,
                    'expected_oee':value.expected_oee,'days':[by_date[d] for d in sorted(by_date)]}
    for equipment_id in equipment_ids:
        for day in sorted(date_keys):
            key=(equipment_id,day)
            times.setdefault(key,{'equipment_id':equipment_id,'work_date':day,'start_time':'08:00',
                'end_time':'08:00','next_day':True,'excluded_minutes':default_excluded_minutes(payload.process_code)})
    times.update(overrides)
    data.update(lines=[lines[key] for key in sorted(lines)],times=[times[key] for key in sorted(times)])
    result=revisions.edit_revision(db,payload.month,number,data,payload.process_code,user,payload.generation,payload.reason)
    return {'message':f'{revisions.label(number)} 내용을 수정했습니다. Rev 번호는 유지됩니다.',
            'generation':result['generation'],'applied_revision':result['applied_revision']}


@router.put('')
def save(payload:MonthInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans','WRITE');validate_process(db,payload.process_code)
    dates=month_days(payload.month);date_keys={d.isoformat() for d in dates}
    seen=set();allowed={i['id'] for i in options_data(db)['items'] if payload.process_code in i['process_codes']}
    existing={ (r.equipment_id,r.item_id):r for r in db.query(ProductionMonthPlanLine).join(EquipmentMaster,EquipmentMaster.id==ProductionMonthPlanLine.equipment_id).filter(ProductionMonthPlanLine.month==payload.month,EquipmentMaster.process_code==payload.process_code)}
    deleted_ids=set(payload.deleted_line_ids)
    scoped_ids={r.id for r in existing.values()}
    if not deleted_ids<=scoped_ids:raise HTTPException(422,'삭제할 행이 선택한 월·공정의 생산계획에 없습니다. 다시 불러오세요.')
    deleted_rows=[r for r in existing.values() if r.id in deleted_ids]
    existing={key:r for key,r in existing.items() if r.id not in deleted_ids}
    # Validate the entire request before editing any saved records.
    for value in payload.lines:
        validate_equipment(db,payload.process_code,value.equipment_id)
        key=(value.equipment_id,value.item_id)
        if key in seen:raise HTTPException(422,'같은 호기·품번이 중복되었습니다.')
        seen.add(key)
        if value.item_id not in allowed and key not in existing:raise HTTPException(422,'선택한 공정의 품번을 선택하세요.')
        if set(value.quantities)!=date_keys:raise HTTPException(422,'해당 월의 일별 계획수량을 모두 입력하세요.')
        row=existing.get(key)
        if row and (row.cycle_seconds!=value.cycle_seconds or row.expected_oee!=value.expected_oee):
            if not dates[0]<=payload.ct_effective_date<=dates[-1]:raise HTTPException(422,'C/T·목표 OEE 변경 적용일은 선택한 월 안에서 지정하세요.')
    equipment_ids={r.equipment_id for r in existing.values()}|{v.equipment_id for v in payload.lines}
    saved_times={(r.equipment_id,r.work_date):r for r in db.query(ProductionPlanWorkTime).filter(ProductionPlanWorkTime.equipment_id.in_(equipment_ids),ProductionPlanWorkTime.work_date.in_(date_keys))}
    overrides={}
    for value in payload.work_times:
        validate_equipment(db,payload.process_code,value.equipment_id)
        key=(value.equipment_id,value.work_date.isoformat())
        if value.equipment_id not in equipment_ids or key[1] not in date_keys:raise HTTPException(422,'가동시간의 호기·날짜가 생산계획과 일치하지 않습니다.')
        if key in overrides:raise HTTPException(422,'같은 호기·날짜의 가동시간이 중복되었습니다.')
        period=bounds(dict(work_date=key[1],working=True,start_time=value.start_time,end_time=value.end_time,next_day=value.next_day))
        previous=saved_times.get(key)
        excluded=value.excluded_minutes if value.excluded_minutes is not None else previous.excluded_minutes if previous else default_excluded_minutes(payload.process_code)
        if excluded*60>=(period[1]-period[0]).total_seconds():raise HTTPException(422,'계획 차감시간은 시작·종료시간 사이보다 짧아야 합니다.')
        overrides[key]=(value,excluded)
    state=revisions.lock(db,payload.month,payload.generation)
    for row in deleted_rows:db.delete(row)
    db.flush()
    # Snapshot every day, including zero-quantity dates. Subsequent default changes cannot rewrite saved hours.
    for equipment_id in equipment_ids:
        for day in date_keys:
            key=(equipment_id,day);saved=saved_times.get(key);override=overrides.get(key)
            if saved is None:
                saved=ProductionPlanWorkTime(equipment_id=equipment_id,work_date=day,start_time='08:00',end_time='08:00',next_day=True,excluded_minutes=default_excluded_minutes(payload.process_code));db.add(saved)
            if override:
                value,excluded=override
                saved.start_time=value.start_time;saved.end_time=value.end_time;saved.next_day=value.next_day;saved.excluded_minutes=excluded
    for value in payload.lines:
        row=existing.get((value.equipment_id,value.item_id));new=row is None
        changed=new or row.cycle_seconds!=value.cycle_seconds or row.expected_oee!=value.expected_oee
        if new:
            row=ProductionMonthPlanLine(month=payload.month,equipment_id=value.equipment_id,item_id=value.item_id,cycle_seconds=value.cycle_seconds,expected_oee=value.expected_oee);db.add(row);db.flush()
        by_date={d.work_date:d for d in row.days}
        for day in sorted(date_keys):
            saved=by_date.get(day)
            if saved is None:
                saved=ProductionMonthPlanDay(line_id=row.id,work_date=day,cycle_seconds=value.cycle_seconds,expected_oee=value.expected_oee);db.add(saved)
            if new or (changed and date.fromisoformat(day)>=payload.ct_effective_date):
                saved.cycle_seconds=value.cycle_seconds;saved.expected_oee=value.expected_oee
            saved.quantity=value.quantities[day]
        row.cycle_seconds=value.cycle_seconds;row.expected_oee=value.expected_oee
    state.saved_by=revisions.actor(user);state.saved_at=datetime.utcnow()
    db.commit();return {'message':f'작업본을 저장했습니다. 삭제 {len(deleted_rows)}행. 개정 반영 전까지 적용 Rev와 OEE 기준은 유지됩니다.','generation':state.generation}


@router.get('/status')
def status(start_date:date,end_date:date,process_code:str|None=None,db:Session=Depends(get_db),user=Depends(get_current_user),revision:int|None=None):
    require_access(user,'/production/plans')
    if end_date<start_date or (end_date-start_date).days>366:raise HTTPException(422,'조회기간은 최대 367일까지 선택하세요.')
    if process_code:validate_process(db,process_code)
    supported={p.process_code:p.process_name for p in machining_processes(db)}
    equipment={e.id:e for e in db.query(EquipmentMaster).filter(EquipmentMaster.process_code.in_(supported))}
    items={i.id:i for i in db.query(ItemMasterModel)}
    selected=None
    if revision is not None:
        if start_date.strftime('%Y-%m')!=end_date.strftime('%Y-%m'):raise HTTPException(422,'Revision 조회는 같은 월 안의 기간을 선택하세요.')
        selected=revisions.snapshot(db,start_date.strftime('%Y-%m'),revision)
    query=revisions.plan_data(db,start_date,end_date,applied=False,selected=selected)[0]
    grouped={};summary={}
    for day,line in query:
        e=equipment.get(line.equipment_id);item=items.get(line.item_id)
        if not e or not item or (process_code and e.process_code!=process_code):continue
        key=(e.id,item.id,day.cycle_seconds,day.expected_oee)
        row=grouped.setdefault(key,dict(equipment_id=e.id,equipment_name=e.equipment_name,process_code=e.process_code,process_name=supported[e.process_code],item_id=item.id,part_no=item.part_no,part_name=item.part_name,cycle_seconds=day.cycle_seconds,expected_oee=day.expected_oee,quantities={},total=0))
        row['quantities'][day.work_date]=day.quantity;row['total']+=day.quantity
        total=summary.setdefault(item.part_no,dict(part_no=item.part_no,part_name=item.part_name,vehicle_model=item.vehicle_model or '',by_process={},total=0))
        total['by_process'][e.process_code]=total['by_process'].get(e.process_code,0)+day.quantity;total['total']+=day.quantity
    rows=sorted(grouped.values(),key=lambda r:(r['process_code'],r['equipment_name'],r['part_no'],r['cycle_seconds']))
    for row in rows:
        uph=math.floor(3600/(row['cycle_seconds']*(2-row['expected_oee'])))
        schedule=days(db,row['process_code'],start_date,end_date,row['equipment_id'],applied=False,selected=selected)
        active=[d for d in schedule if row['quantities'].get(d['work_date'],0)>0]
        hours=[net_hours(d) for d in active]
        daytime=[daytime_hours(d,row['process_code']) for d in active]
        row.update(uph=uph,upd=math.floor(uph*standard_daytime_hours(row['process_code'])),upm_day=math.floor(round(uph*sum(daytime),8)),upm_full=math.floor(round(uph*sum(hours),8)))
    return dict(revision_context='작업본' if revision is None else f'{start_date:%Y-%m} {revisions.label(revision)}',dates=[(start_date+timedelta(days=i)).isoformat() for i in range((end_date-start_date).days+1)],rows=rows,summary=sorted(summary.values(),key=lambda r:(r['vehicle_model'],r['part_no'])),processes=[dict(code=k,name=v) for k,v in supported.items() if not process_code or k==process_code],total=sum(s['total'] for s in summary.values()))


class RevisionAction(BaseModel):
    month:str
    generation:int=Field(ge=0)
    reason:str=Field(min_length=1,max_length=1000)
    @field_validator('reason')
    @classmethod
    def reason_required(cls,value):
        if not value.strip():raise ValueError('변경 사유를 입력하세요.')
        return value.strip()


class ApplyRevision(RevisionAction):
    number:int=Field(ge=0)


@router.get('/revisions')
def revision_list(month:str,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans');month_days(month)
    return revisions.details(db,month)


@router.post('/revisions')
def create_revision(payload:RevisionAction,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans','WRITE');month_days(payload.month)
    return revisions.publish(db,payload.month,payload.reason,user,payload.generation)


@router.post('/revisions/apply')
def apply_revision(payload:ApplyRevision,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans','WRITE');month_days(payload.month)
    return revisions.apply(db,payload.month,payload.number,payload.reason,user,payload.generation)
