import math
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user, check_admin_permission, parse_user_permissions, check_permission
from models.models import ItemMasterModel, ItemBomModel
from models.production_cycle_time import ProductionCycleTime
from services.equipment_oee import lathe_codes, calculate

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
    codes=lathe_codes(db)
    bom_items={b.parent_item_id for b in db.query(ItemBomModel).filter(ItemBomModel.process_code.in_(codes))}
    registered={c.item_id:c for c in db.query(ProductionCycleTime)}
    return [dict(item_id=i.id,part_no=i.part_no,part_name=i.part_name,seconds=registered[i.id].seconds if i.id in registered else None) for i in db.query(ItemMasterModel).filter(ItemMasterModel.is_active=='Y').order_by(ItemMasterModel.part_no) if i.material_type not in ('RAW','원재료') and (i.production_loc in codes or i.id in bom_items or i.part_no.upper().endswith('-A'))]


class CycleInput(BaseModel):
    seconds:float=Field(gt=0,le=86400)
    @field_validator('seconds')
    @classmethod
    def finite(cls,value):
        if not math.isfinite(value):raise ValueError('유한한 사이클타임을 입력하세요.')
        return value


@router.get('/basic-info/cycle-times',response_class=HTMLResponse)
def page(request:Request,user=Depends(get_current_user)):
    require_access(user,'/basic-info/cycle-times')
    return Jinja2Templates(directory='templates').TemplateResponse(request=request,name='equipment_oee.html',context={'request':request,'user':user,'cycle_mode':True})


@router.get('/api/production/cycle-times')
def cycles(db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/basic-info/cycle-times')
    return {'items':cycle_items(db)}


@router.put('/api/production/cycle-times/{item_id}')
def save(item_id:int,payload:CycleInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/basic-info/cycle-times','WRITE')
    if item_id not in {i['item_id'] for i in cycle_items(db)}:raise HTTPException(404,'사용 가능한 복합선반 품번을 찾을 수 없습니다.')
    row=db.get(ProductionCycleTime,item_id)
    if row is None:row=ProductionCycleTime(item_id=item_id);db.add(row)
    row.seconds=payload.seconds;db.commit()
    return {'message':'사이클타임을 저장했습니다.'}


@router.delete('/api/production/cycle-times/{item_id}')
def remove(item_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/basic-info/cycle-times','WRITE')
    row=db.get(ProductionCycleTime,item_id)
    if row is None:raise HTTPException(404,'등록된 사이클타임이 없습니다.')
    db.delete(row);db.commit();return {'message':'사이클타임을 삭제했습니다.'}


@router.get('/api/production/equipment/oee')
def report(start_date:date,end_date:date,db:Session=Depends(get_db),user=Depends(get_current_user)):
    require_access(user,'/production/equipment/status')
    if end_date<start_date or (end_date-start_date).days>366:raise HTTPException(422,'조회기간은 시작일부터 최대 367일까지 선택하세요.')
    return calculate(db,start_date,end_date)
