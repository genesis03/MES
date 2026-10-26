import math
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user, check_admin_permission, parse_user_permissions, check_permission
from models.models import ItemMasterModel, ProcessModel
from services.production_sync_mapping import stage_suffix
from models.equipment import EquipmentMaster
from models.production_cycle_time import ProductionCycleTime
from services.equipment_oee import calculate
from services.production_process_type import performance_type_for_process
from services.oee_schedule import month_days, days

router=APIRouter(tags=['Equipment OEE'])

def require_access(user, menu, action='READ'):
    if check_admin_permission(user):return
    module='basic_info' if menu.startswith('/basic-info/') else 'production'
    access=parse_user_permissions(user).get('menu_access')
    if isinstance(access,dict) and access:
        level=access.get(menu,'NONE')
        level='READ' if level is True else str(level).upper()
        allowed=level=='WRITE' if action=='WRITE' else level in ('READ','WRITE')
    else:allowed=check_permission(user,module,action)
    if not allowed:raise HTTPException(403,'해당 메뉴의 '+('쓰기' if action=='WRITE' else '조회')+' 권한이 필요합니다.')



def cycle_items(db):
    codes=[p.process_code for p in machining_processes(db)]
    registered={c.item_id:c for c in db.query(ProductionCycleTime)}
    return [dict(item_id=i.id,part_no=i.part_no,part_name=i.part_name,seconds=registered[i.id].seconds if i.id in registered else None) for i in db.query(ItemMasterModel).filter(ItemMasterModel.is_active=='Y').order_by(ItemMasterModel.part_no) if i.material_type not in ('RAW','원재료') and i.production_loc in codes]


class CycleInput(BaseModel):
    seconds:float=Field(gt=0,le=86400)
    @field_validator('seconds')
    @classmethod
    def finite(cls,value):
        if not math.isfinite(value):raise ValueError('유한한 사이클타임을 입력하세요.')
        return value


@router.get('/basic-info/cycle-times',response_class=HTMLResponse)
def page(request:Request,user=Depends(get_current_user)):
    require_access(user,'/basic-info/items')
    return RedirectResponse('/basic-info/items',status_code=303)


@router.get('/api/production/cycle-times')
def cycles(db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/basic-info/items')
    return {'items':cycle_items(db)}


@router.put('/api/production/cycle-times/{item_id}')
def save(item_id:int,payload:CycleInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/basic-info/items','WRITE')
    if item_id not in {i['item_id'] for i in cycle_items(db)}:raise HTTPException(404,'사용 가능한 가공 품번을 찾을 수 없습니다.')
    row=db.get(ProductionCycleTime,item_id)
    if row is None:row=ProductionCycleTime(item_id=item_id);db.add(row)
    row.seconds=payload.seconds;db.commit()
    return {'message':'사이클타임을 저장했습니다.'}


@router.delete('/api/production/cycle-times/{item_id}')
def remove(item_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/basic-info/items','WRITE')
    row=db.get(ProductionCycleTime,item_id)
    if row is None:raise HTTPException(404,'등록된 사이클타임이 없습니다.')
    db.delete(row);db.commit();return {'message':'사이클타임을 삭제했습니다.'}


@router.get('/api/production/equipment/oee')
def report(start_date:date,end_date:date,process_code:str|None=None,exclude_unplanned:bool=False,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/equipment/status')
    if end_date<start_date or (end_date-start_date).days>366:raise HTTPException(422,'조회기간은 시작일부터 최대 367일까지 선택하세요.')
    if process_code:validate_process(db,process_code)
    return calculate(db,start_date,end_date,process_code=process_code,exclude_unplanned=exclude_unplanned)


def machining_processes(db):
    return [p for p in db.query(ProcessModel).filter(ProcessModel.is_active=='Y').order_by(ProcessModel.sort_order,ProcessModel.id) if stage_suffix(p.process_name) in ('-A','-B')]


def validate_process(db,code):
    if code not in {p.process_code for p in machining_processes(db)}:raise HTTPException(422,'복합선반 또는 탭핑 공정을 선택하세요.')


@router.get('/api/production/equipment/oee/processes')
def processes(db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/equipment/status')
    return {'items':[dict(process_code=p.process_code,process_name=p.process_name) for p in machining_processes(db)]}


@router.get('/production/equipment/efficiency',response_class=HTMLResponse)
def efficiency_page(request:Request,user=Depends(get_current_user)):
    require_access(user,'/production/equipment/efficiency')
    return Jinja2Templates(directory='templates').TemplateResponse(request=request,name='equipment_efficiency.html',context={'request':request,'user':user})


@router.get('/production/equipment/daily',response_class=HTMLResponse)
def daily_page(request:Request,user=Depends(get_current_user)):
    require_access(user,'/production/equipment/daily')
    return Jinja2Templates(directory='templates').TemplateResponse(request=request,name='equipment_daily.html',context={'request':request,'user':user})


@router.get('/api/production/equipment/report-options')
def report_options(view:str=Query(default='efficiency',pattern=r'^(efficiency|daily)$'),db:Session=Depends(get_db),user=Depends(get_current_user)):
    from sqlalchemy import func
    from models.production import ProductionPerformance
    from models.production_month_plan import ProductionMonthPlanLine
    from models.production_sync import ExternalProductionRecord
    from services.equipment_reports import report_now
    require_access(user,'/production/equipment/'+view)
    processes=machining_processes(db);codes=[p.process_code for p in processes]
    years={report_now().year}
    for model,column in ((ProductionMonthPlanLine,ProductionMonthPlanLine.month),(ProductionPerformance,ProductionPerformance.performance_date),(ExternalProductionRecord,ExternalProductionRecord.work_date)):
        for (value,) in db.query(func.substr(column,1,4)).distinct():
            if value and value.isdigit() and 2000<=int(value)<=2100:years.add(int(value))
    return dict(years=sorted(years,reverse=True),processes=[dict(process_code=p.process_code,process_name=p.process_name) for p in processes],
                equipments=[dict(id=e.id,name=e.equipment_name,code=e.equipment_code,process_code=e.process_code,active=e.is_active=='Y')
                            for e in db.query(EquipmentMaster).filter(EquipmentMaster.process_code.in_(codes)).order_by(EquipmentMaster.process_code,EquipmentMaster.sort_order,EquipmentMaster.id)])


@router.get('/api/production/equipment/efficiency')
def efficiency(year:int=Query(ge=2000,le=2100),month:int=Query(ge=1,le=12),process_code:str|None=None,exclude_unplanned:bool=False,db:Session=Depends(get_db),user=Depends(get_current_user)):
    from services.equipment_reports import efficiency_report
    require_access(user,'/production/equipment/efficiency')
    if process_code:validate_process(db,process_code)
    return efficiency_report(db,year,month,process_code,exclude_unplanned)


@router.get('/api/production/equipment/daily')
def daily(month:str=Query(pattern=r'^\d{4}-\d{2}$'),equipment_id:int=Query(gt=0),exclude_unplanned:bool=False,db:Session=Depends(get_db),user=Depends(get_current_user)):
    from services.equipment_reports import daily_report
    require_access(user,'/production/equipment/daily')
    dates=month_days(month)
    if not 2000<=dates[0].year<=2100:raise HTTPException(422,'조회 연도는 2000~2100년 중 선택하세요.')
    e=db.get(EquipmentMaster,equipment_id)
    if e is None or e.process_code not in {p.process_code for p in machining_processes(db)}:raise HTTPException(422,'복합선반 또는 탭핑 호기를 선택하세요.')
    return daily_report(db,month,equipment_id,exclude_unplanned)


class ScheduleDayInput(BaseModel):
    work_date:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    working:bool
    start_time:str=Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    end_time:str=Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    next_day:bool=True


class ScheduleMonthInput(BaseModel):
    equipment_id:int|None=None
    month:str
    process_code:str
    items:list[ScheduleDayInput]=Field(min_length=28,max_length=31)


@router.get('/api/production/equipment/schedule')
def get_schedule(month:str,process_code:str,equipment_id:int|None=None,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans')
    validate_process(db,process_code)
    if equipment_id is not None:validate_equipment(db,process_code,equipment_id)
    dates=month_days(month)
    return {'items':days(db,process_code,dates[0],dates[-1],equipment_id),'month':month,'process_code':process_code}


@router.put('/api/production/equipment/schedule')
def update_schedule(payload:ScheduleMonthInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/plans','WRITE')
    raise HTTPException(409,'가동일은 생산계획 세부내용의 일별 수량으로 자동 계산됩니다. 생산계획에서 입력하세요.')


def validate_equipment(db,process_code,equipment_id):
    equipment=db.get(EquipmentMaster,equipment_id)
    if equipment is None or equipment.process_code!=process_code or equipment.is_active!='Y':raise HTTPException(422,'선택한 공정의 생산호기를 선택하세요.')
    return equipment
