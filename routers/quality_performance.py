import json
import re
from urllib.parse import quote
from datetime import date,datetime,timedelta
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user,check_admin_permission,parse_user_permissions
from models.quality_performance import QualityPerformanceMonth,QualityPerformanceAudit
from services.quality_performance_service import build_report,blank_manual,GOALS,CLAIM_GROUPS
from services.quality_performance_excel import build_excel
from services.quality_performance_form import build_form
from services.production_sync_service import today

router=APIRouter(tags=['Quality Performance'])
MENU='/quality/performance';API='/api/quality/performance'
templates=Jinja2Templates(directory='templates')


def access(user,write=False):
    if check_admin_permission(user):return
    permissions=parse_user_permissions(user).get('menu_access',{})
    level=permissions.get(MENU,'NONE') if isinstance(permissions,dict) else 'NONE'
    if level is True:level='READ'
    if str(level).upper() not in (('WRITE',) if write else ('READ','WRITE')):raise HTTPException(403,'품질실적 '+('작성' if write else '조회')+' 권한이 없습니다.')


def month_value(value):
    if not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])',value):raise HTTPException(422,'보고 월을 확인하세요.')
    if date.fromisoformat(value+'-01')>=today().replace(day=1):raise HTTPException(422,'전월까지의 실적을 선택하세요.')
    return value


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class Claim(Input):
    occurred:float|None=Field(default=None,ge=0,le=1e15,allow_inf_nan=False)
    agreed:float|None=Field(default=None,ge=0,le=1e15,allow_inf_nan=False)
    deducted:float|None=Field(default=None,ge=0,le=1e15,allow_inf_nan=False)


class Note(Input):
    cause:str=Field(default='',max_length=3000)
    action:str=Field(default='',max_length=3000)


class Manual(Input):
    delivery_qty:int|None=Field(default=None,ge=0,le=10**12)
    customer_bad:int|None=Field(default=None,ge=0,le=10**12)
    customer_cases:int|None=Field(default=None,ge=0,le=10**9)
    revenue:float|None=Field(default=None,ge=0,le=1e15,allow_inf_nan=False)
    claims:dict[str,Claim]
    author:str=Field(default='',max_length=100)
    notes:dict[str,Note]
    @model_validator(mode='after')
    def keys(self):
        if set(self.claims)!=set(CLAIM_GROUPS) or set(self.notes)!=set(GOALS):raise ValueError('수기 항목 구분을 확인하세요.')
        return self


class Save(Input):
    month:str
    version:int=Field(ge=0)
    manual:Manual


@router.get(MENU)
def page(request:Request,user=Depends(get_current_user)):
    access(user)
    try:access(user,True);write=True
    except HTTPException:write=False
    month=(today().replace(day=1)-timedelta(days=1)).strftime('%Y-%m')
    return templates.TemplateResponse(request=request,name='quality_performance.html',context={'request':request,'user':user,'can_write':write,'report_month':month})


@router.get(API)
def report(month:str,source:Literal['EXTERNAL','MES']='EXTERNAL',db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user);return build_report(db,month_value(month),source)


@router.get(API+'/form',name='quality_performance_form')
def form(month:str,source:Literal['EXTERNAL','MES']='EXTERNAL',db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    data=build_report(db,month_value(month),source)
    return build_form(data,today(),data['manual']['author'] or user.username)


@router.put(API)
def save(payload:Save,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);month=month_value(payload.month);data=payload.manual.model_dump(mode='json')
    row=db.query(QualityPerformanceMonth).filter_by(month=month).one_or_none();before=row.data_json if row else '{}'
    actor=user.username
    if row is None:
        if payload.version!=0:raise HTTPException(409,'월별 기록이 변경되었습니다. 다시 조회하세요.')
        row=QualityPerformanceMonth(month=month,version=1,data_json=json.dumps(data,ensure_ascii=False),updated_by=actor);db.add(row)
        try:db.flush()
        except IntegrityError:db.rollback();raise HTTPException(409,'다른 사용자가 먼저 저장했습니다. 입력을 보관하고 다시 조회하세요.')
    else:
        changed=db.execute(update(QualityPerformanceMonth).where(QualityPerformanceMonth.id==row.id,QualityPerformanceMonth.version==payload.version).values(version=payload.version+1,data_json=json.dumps(data,ensure_ascii=False),updated_by=actor,updated_at=datetime.now()))
        if changed.rowcount!=1:db.rollback();raise HTTPException(409,'다른 사용자가 수정했습니다. 입력을 보관하고 다시 조회하세요.')
    db.add(QualityPerformanceAudit(month=month,before_json=before,after_json=json.dumps(data,ensure_ascii=False),actor=actor));db.commit()
    return {'version':row.version,'message':'수기 실적을 저장했습니다.'}


@router.get(API+'/excel')
def excel(month:str,source:Literal['EXTERNAL','MES']='EXTERNAL',db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user);data=build_report(db,month_value(month),source)
    author=data['manual']['author'] or user.username
    stream=build_excel(data,today(),author)
    return StreamingResponse(stream,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f"attachment; filename=QualityPerformance_{month.replace('-', '')}.xlsx; filename*=UTF-8''{quote('품질실적보고_'+month.replace('-', '')+'.xlsx')}"})


@router.get(API+'/history')
def history(month:str,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user);month_value(month)
    return [{'at':r.created_at.isoformat(timespec='seconds'),'actor':r.actor,'before':json.loads(r.before_json),'after':json.loads(r.after_json)} for r in db.query(QualityPerformanceAudit).filter_by(month=month).order_by(QualityPerformanceAudit.id.desc()).limit(100)]
