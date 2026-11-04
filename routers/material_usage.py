import json
from datetime import date,datetime
from decimal import Decimal
from io import BytesIO
from typing import Annotated
from fastapi import APIRouter,Depends,HTTPException,Query,Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel,ConfigDict,Field,StringConstraints,model_validator
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from core import security
from core.database import get_db
from core.security import get_current_user,check_admin_permission,parse_user_permissions
from models.material_usage import MaterialUsageRecord,MaterialUsageAudit
from services.material_usage_service import serialize

router=APIRouter(tags=['Material Usage'])
templates=Jinja2Templates(directory='templates')
MENU='/inventory/material-usage';API='/api/inventory/material-usage'
Text=Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=100)]
Qty=Annotated[Decimal,Field(ge=0,max_digits=20,decimal_places=6,allow_inf_nan=False)]


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class Material(Input):
    part_no:Text
    lot_no:str=Field(default='',max_length=100)
    unit:Text='EA'
    expected_qty:Qty|None=None
    issued_qty:Qty|None=None
    used_qty:Qty|None=None
    scrap_qty:Qty|None=None
    remaining_qty:Qty|None=None
    note:str=Field(default='',max_length=1000)


class RecordInput(Input):
    work_date:date
    worker:Text
    part_no:Text
    production_lot:str=Field(default='',max_length=100)
    production_qty:Qty
    note:str=Field(default='',max_length=1000)
    materials:list[Material]=Field(default_factory=list,max_length=500)
    @model_validator(mode='after')
    def unique_requirement(self):
        seen=set()
        for row in self.materials:
            key=(row.part_no.casefold(),row.unit.casefold())
            if row.expected_qty is not None:
                if key in seen:raise ValueError('같은 자재의 총 필요수량은 한 행에만 입력하세요.')
                seen.add(key)
        return self


class CreateInput(RecordInput):
    request_key:Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=80)]


class EditInput(RecordInput):
    version:int=Field(ge=1)
    reason:Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=1000)]


class CancelInput(Input):
    version:int=Field(ge=1)
    reason:Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=1000)]


def access(user,write=False):
    if security.DEV_BYPASS_AUTH or check_admin_permission(user):return
    level=parse_user_permissions(user).get('menu_access',{}).get(MENU,'NONE') if user else 'NONE'
    if level not in (('WRITE',) if write else ('READ','WRITE',True)):raise HTTPException(403,'자재 사용 현황 '+('수정' if write else '조회')+' 권한이 없습니다.')


def get_record(db,identifier):
    record=db.get(MaterialUsageRecord,identifier)
    if not record:raise HTTPException(404,'사용 기록을 찾을 수 없습니다.')
    return record


def audit(db,row,action,before,after,reason,user):
    db.add(MaterialUsageAudit(record_id=row.id,action=action,before_json=json.dumps(before,ensure_ascii=False),after_json=json.dumps(after,ensure_ascii=False),reason=reason,actor=user.username))


def data_for(payload):return payload.model_dump(mode='json',exclude={'request_key','version','reason'})


def query_records(db,start,end,part_no,worker,include_cancelled):
    if start>end:raise HTTPException(422,'조회 시작일과 종료일을 확인하세요.')
    query=db.query(MaterialUsageRecord).filter(MaterialUsageRecord.work_date.between(start.isoformat(),end.isoformat()))
    if not include_cancelled:query=query.filter(MaterialUsageRecord.cancelled.is_(False))
    if part_no.strip():query=query.filter(MaterialUsageRecord.part_no.contains(part_no.strip(),autoescape=True))
    if worker.strip():query=query.filter(MaterialUsageRecord.worker.contains(worker.strip(),autoescape=True))
    return query.order_by(MaterialUsageRecord.work_date.desc(),MaterialUsageRecord.id.desc())


@router.get(MENU)
def page(request:Request,user=Depends(get_current_user)):
    access(user)
    try:access(user,True);can_write=True
    except HTTPException:can_write=False
    return templates.TemplateResponse(request=request,name='material_usage.html',context={'request':request,'user':user,'can_write':can_write})


@router.get(API)
def listing(start:date,end:date,part_no:str=Query('',max_length=100),worker:str=Query('',max_length=100),flag:str=Query('',max_length=50),include_cancelled:bool=False,page:int=Query(1,ge=1),size:int=Query(50,ge=1,le=200),db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    rows=[serialize(row) for row in query_records(db,start,end,part_no,worker,include_cancelled)]
    if flag:rows=[row for row in rows if flag in row['flags']]
    return {'total':len(rows),'page':page,'size':size,'items':rows[(page-1)*size:page*size]}


@router.get(API+'/export')
def export(start:date,end:date,part_no:str=Query('',max_length=100),worker:str=Query('',max_length=100),flag:str=Query('',max_length=50),include_cancelled:bool=False,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    from openpyxl import Workbook
    book=Workbook();sheet=book.active;sheet.title='자재 사용 현황'
    sheet.append(['일자','작업자','생산품번','생산 LOT','조립수량','자재품번','자재 LOT','단위','필요수량','투입','사용','폐기','잔여','수불차이','확인사항','비고','상태'])
    def safe(value):return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value
    def qty(value):return Decimal(value) if value is not None else None
    for record in query_records(db,start,end,part_no,worker,include_cancelled):
        row=serialize(record)
        if flag and flag not in row['flags']:continue
        for m in row['materials'] or [None]:
            values=[row['work_date'],row['worker'],row['part_no'],row['production_lot'],qty(row['production_qty'])]
            values+=([m['part_no'],m['lot_no'],m['unit']]+[qty(m[k]) for k in ['expected_qty','issued_qty','used_qty','scrap_qty','remaining_qty','balance_difference']]) if m else ['']*9
            sheet.append([safe(v) for v in values+[' / '.join(row['flags']),row['note']+(' / '+m['note'] if m and m['note'] else ''),'취소' if row['cancelled'] else '유효']])
    sheet.freeze_panes='F2';sheet.auto_filter.ref=sheet.dimensions
    for column in sheet.columns:sheet.column_dimensions[column[0].column_letter].width=18
    info=book.create_sheet('검증 기준');info.append(['전체 수기 기록이며 재고·생산실적·BOM·LOT를 생성하거나 변경하지 않습니다.']);info.append(['입력한 자재행만 비교합니다. 기록 자체가 누락된 자재나 실제 조립 여부를 자동 확정하지 않습니다.']);info.append(['필요수량은 같은 자재·단위의 전체 필요수량을 한 행에만 입력하며 사용량은 LOT별로 합산합니다.'])
    stream=BytesIO();book.save(stream);stream.seek(0)
    return StreamingResponse(stream,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="material-usage.xlsx"'})


@router.post(API)
def create(payload:CreateInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);data=data_for(payload)
    existing=db.query(MaterialUsageRecord).filter_by(request_key=payload.request_key).first()
    if existing:
        if json.loads(existing.data_json)!=data:raise HTTPException(409,'이미 저장된 등록 요청입니다. 새 작성으로 등록하세요.')
        return serialize(existing)
    row=MaterialUsageRecord(request_key=payload.request_key,work_date=payload.work_date.isoformat(),worker=payload.worker,part_no=payload.part_no,data_json=json.dumps(data,ensure_ascii=False),created_by=user.username,updated_by=user.username)
    try:
        db.add(row);db.flush();audit(db,row,'CREATE',{},data,'신규 작성',user);db.commit()
    except IntegrityError:db.rollback();raise HTTPException(409,'이미 저장 중인 기록입니다. 목록에서 확인하세요.')
    return serialize(row)


@router.get(API+'/{identifier}')
def detail(identifier:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user);return serialize(get_record(db,identifier))


@router.put(API+'/{identifier}')
def edit(identifier:int,payload:EditInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);row=get_record(db,identifier);before=json.loads(row.data_json);data=data_for(payload)
    result=db.execute(update(MaterialUsageRecord).where(MaterialUsageRecord.id==identifier,MaterialUsageRecord.version==payload.version,MaterialUsageRecord.cancelled.is_(False)).values(work_date=payload.work_date.isoformat(),worker=payload.worker,part_no=payload.part_no,data_json=json.dumps(data,ensure_ascii=False),version=payload.version+1,updated_by=user.username,updated_at=datetime.now()))
    if result.rowcount!=1:db.rollback();raise HTTPException(409,'취소되었거나 다른 사용자가 수정했습니다. 입력을 보관하고 다시 조회하세요.')
    audit(db,row,'EDIT',before,data,payload.reason,user);db.commit();return serialize(row)


@router.post(API+'/{identifier}/cancel')
def cancel(identifier:int,payload:CancelInput,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);row=get_record(db,identifier)
    result=db.execute(update(MaterialUsageRecord).where(MaterialUsageRecord.id==identifier,MaterialUsageRecord.version==payload.version,MaterialUsageRecord.cancelled.is_(False)).values(cancelled=True,version=payload.version+1,updated_by=user.username,updated_at=datetime.now()))
    if result.rowcount!=1:db.rollback();raise HTTPException(409,'이미 취소되었거나 다른 사용자가 수정했습니다. 다시 조회하세요.')
    audit(db,row,'CANCEL',json.loads(row.data_json),{'cancelled':True},payload.reason,user);db.commit();return {'status':'cancelled'}


@router.get(API+'/{identifier}/history')
def history(identifier:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user);get_record(db,identifier)
    return [{'action':row.action,'actor':row.actor,'at':row.created_at.isoformat(timespec='seconds'),'reason':row.reason,'before':json.loads(row.before_json),'after':json.loads(row.after_json)} for row in db.query(MaterialUsageAudit).filter_by(record_id=identifier).order_by(MaterialUsageAudit.id.desc())]
