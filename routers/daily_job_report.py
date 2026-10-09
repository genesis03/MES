import io,math
from datetime import date
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import Response,HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel,Field,field_validator
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from models.daily_job_report import DailyJobReportSupplement
from models.production_run import ProductionRun
from models.production_sync import ExternalProductionRecord
from models.inspection_standard import InspectionStandard
from routers.production_extra import production_performance_status
from services.daily_job_report import build_workbook,workbook_html

router=APIRouter(tags=['Daily job report'])

class Measurement(BaseModel):
    label:str=Field(default='',max_length=200)
    spec:str=Field(default='',max_length=500)
    values:list[float|None]=Field(default_factory=lambda:[None]*3,min_length=3,max_length=3)
    worker_result:Literal['','OK','NG']=''
    quality_result:Literal['','OK','NG']=''
    @field_validator('values')
    @classmethod
    def finite(cls,values):
        if any(v is not None and not math.isfinite(v) for v in values):raise ValueError('측정값은 유한한 숫자여야 합니다.')
        return values

class Supplement(BaseModel):
    operator_name:str=Field(default='',max_length=50)
    shift:Literal['','주간','야간']=''
    time:str=Field(default='',max_length=100)
    raw_input:int|None=Field(default=None,ge=0)
    raw_remaining:int|None=Field(default=None,ge=0)
    cut_bars:int|None=Field(default=None,ge=0)
    raw_setup:int|None=Field(default=None,ge=0)
    inspection_kind:Literal['','SET-UP 검사','자주검사']=''
    inspection_time:str=Field(default='',max_length=50)
    measurements:list[Measurement]=Field(default_factory=list,max_length=100)
    appearance:str=Field(default='',max_length=200)
    notes:str=Field(default='',max_length=2000)

class Selection(BaseModel):
    day:date
    keys:list[str]=Field(min_length=1,max_length=100)


def records(db,day,user):
    rows=production_performance_status(start_date=day.isoformat(),end_date=day.isoformat(),performance_type=None,
        process_code=None,work_order_no=None,part_no=None,operator_name=None,db=db,current_user=user)
    supplements={(s.record_source,s.record_id):s for s in db.query(DailyJobReportSupplement)}
    runs={r.performance_id:r for r in db.query(ProductionRun).filter(ProductionRun.performance_date==day.isoformat(),ProductionRun.status=='COMPLETED')}
    for row in rows:
        row['key']=row['record_source']+':'+str(row['source_record_id'])
        saved=supplements.get((row['record_source'],row['source_record_id']))
        supplement=Supplement.model_validate_json(saved.data_json) if saved else Supplement()
        run=runs.get(row['source_record_id']) if row['record_source']=='MES' else None
        row['defects']=[{'name':d.defect_type_name,'qty':d.defect_qty} for d in run.defects] if run else []
        row['work_time']='';row['material_lots']=row['source_lot_no']
        if run:
            row['work_time']=run.start_time+' ~ '+(run.end_time or '')
            row['material_lots']=' / '.join(f'{a.lot_no}: {a.allocated_qty:g} {m.unit}' for m in run.materials for a in m.allocations)
        elif row['record_source']=='EXTERNAL':
            external=db.get(ExternalProductionRecord,row['source_record_id'])
            row['work_time']=' ~ '.join(str(x or '') for x in [external.started_at,external.ended_at])
        if not saved and row.get('item_id'):
            standards=db.query(InspectionStandard).filter(InspectionStandard.item_id==row['item_id'],InspectionStandard.document_type=='PROCESS',InspectionStandard.status=='CURRENT',InspectionStandard.inspection_process_code==row['process_code']).all()
            # Only a unique applicable current standard can supply inspection labels.
            if len(standards)==1:
                supplement.measurements=[Measurement(label=i.inspection_item_name,spec=i.spec_text or '') for i in standards[0].items]
        row['supplement']=supplement.model_dump()
    return rows


def selected(db,payload,user):
    rows={row['key']:row for row in records(db,payload.day,user)}
    keys=list(dict.fromkeys(payload.keys))
    if any(key not in rows for key in keys):raise HTTPException(409,'선택한 생산실적이 변경되거나 삭제되었습니다. 다시 조회해 주세요.')
    return [rows[key] for key in keys]

@router.get('/production/daily-job-report',response_class=HTMLResponse)
def page(request:Request,user=Depends(get_current_user)):
    return Jinja2Templates(directory='templates').TemplateResponse(request=request,name='daily_job_report.html',context={'request':request,'user':user})

@router.get('/api/production/daily-job-report')
def list_records(day:date,db:Session=Depends(get_db),user=Depends(get_current_user)):
    return {'items':records(db,day,user)}

@router.put('/api/production/daily-job-report/{source}/{record_id}')
def save(source:Literal['MES','EXTERNAL'],record_id:int,data:Supplement,day:date,db:Session=Depends(get_db),user=Depends(get_current_user)):
    selected(db,Selection(day=day,keys=[source+':'+str(record_id)]),user)
    row=db.query(DailyJobReportSupplement).filter_by(record_source=source,record_id=record_id).first()
    if not row:
        row=DailyJobReportSupplement(record_source=source,record_id=record_id);db.add(row)
    row.data_json=data.model_dump_json();row.updated_by=str(getattr(user,'username',''))
    db.commit();return {'message':'작업일보 보충 내용을 저장했습니다.'}

@router.post('/api/production/daily-job-report/excel')
def export(payload:Selection,db:Session=Depends(get_db),user=Depends(get_current_user)):
    workbook=build_workbook(selected(db,payload,user));buffer=io.BytesIO();workbook.save(buffer)
    return Response(buffer.getvalue(),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="DailyJobReport_{payload.day.isoformat()}.xlsx"'})

@router.post('/api/production/daily-job-report/print',response_class=HTMLResponse)
def print_report(payload:Selection,db:Session=Depends(get_db),user=Depends(get_current_user)):
    return HTMLResponse(workbook_html(build_workbook(selected(db,payload,user))))
