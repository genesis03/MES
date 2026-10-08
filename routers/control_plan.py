"""Control plan drafts, independent revisions and atomic Excel validation."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from core.database import get_db
from services.document_correction_service import with_correction_token, validate_correction, record_correction
from core.security import get_current_user, check_admin_permission, parse_user_permissions
from models.control_plan import ControlPlanRevision, ControlPlanInspectionLink
from models.models import ItemMasterModel, ProcessModel, UserModel
from services.inspection_standard_links import INTERNAL_CODES, CATEGORIES
from models.process_flow import ProcessFlowRevision
from services.document_service import lock_item
from services.standard_document_item_service import selectable_finished_items, require_finished_item, is_selectable_finished_item
from services.process_flow_service import flow_dict, usable_flow, flow_steps
from services.revision_number_service import normalize_revision_code
from services.control_plan_excel import parse_control_plan, ControlPlanImportError, MAX_BYTES

router=APIRouter(prefix='/api/control-plans',tags=['Control Plans'])
MENU='/standard-documents/control-plans'
HEADER_NAMES={'company','vehicle_model','part_name','part_no','team','established_date','supplier_code',
              'customer_engineering_approval','customer_quality_approval','other_approval',
              *(f'{person}_{field}' for person in ('prepared','reviewed','approved') for field in ('signature','date')),
              *(f'revision_{n}_{field}' for n in range(1,7) for field in ('date','reason','prepared','reviewed','approved'))}

def cp_revision_code(value):
    code=normalize_revision_code(value)
    return 'REV.'+(code[4:].lstrip('0') or '0')

class Row(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    flow_step_id:int=Field(gt=0)
    process_detail:str=Field(default='',max_length=4000)
    equipment:str=Field(default='',max_length=4000)
    item_no:str=Field(default='',max_length=4000)
    product:str=Field(default='',max_length=4000)
    process:str=Field(default='',max_length=4000)
    classification:str=Field(default='',max_length=4000)
    specification:str=Field(default='',max_length=4000)
    method:str=Field(default='',max_length=4000)
    sample_size:str=Field(default='',max_length=4000)
    sample_frequency:str=Field(default='',max_length=4000)
    control_method:str=Field(default='',max_length=4000)
    reaction:str=Field(default='',max_length=4000)
    note:str=Field(default='',max_length=4000)
    sub:str=Field(default='',max_length=4000)
    main:str=Field(default='',max_length=4000)
    outside:str=Field(default='',max_length=4000)
    fool_proof:bool=False
    automatic:bool=False
    material:bool=False
    production:bool=False
    quality:bool=False
    engineering:bool=False

class Save(BaseModel):
    correction_reason:str=Field(default='',max_length=4000)
    correction_token:str=Field(default='',max_length=64)
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    item_id:int=Field(gt=0)
    document_no:str=Field(min_length=1,max_length=100)
    revision_code:str=Field(min_length=1,max_length=50)
    _normalize=field_validator('revision_code',mode='before')(cp_revision_code)
    flow_revision_id:int=Field(gt=0)
    flow_version:int=Field(gt=0)
    version:int|None=Field(default=None,gt=0)
    header:dict[str,str|bool]=Field(default_factory=dict,max_length=60)
    rows:list[Row]=Field(default_factory=list,max_length=500)

    @field_validator('header')
    @classmethod
    def valid_header(cls,value):
        for key,text in value.items():
            if key in {'stage_prototype','stage_prelaunch','stage_production'}:
                if not isinstance(text,bool): raise ValueError('단계는 체크박스 값이어야 합니다.')
            elif key not in HEADER_NAMES or not isinstance(text,str) or len(text)>4000:
                raise ValueError('상단 입력 항목 또는 길이가 올바르지 않습니다.')
            elif key.endswith('_date') and text:
                from datetime import date
                date.fromisoformat(text)
        return value

class Version(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:int=Field(gt=0)

class Revise(Version):
    revision_code:str=Field(min_length=1,max_length=50)
    _normalize=field_validator('revision_code',mode='before')(cp_revision_code)


def access(user,write=False):
    if check_admin_permission(user): return
    permissions=parse_user_permissions(user).get('menu_access',{})
    value=permissions.get(MENU) if isinstance(permissions,dict) else None
    level='READ' if value is True else str(value or 'NONE').upper()
    if level not in ({'WRITE'} if write else {'READ','WRITE'}):
        raise HTTPException(403,'관리계획서 쓰기 권한이 필요합니다.' if write else '관리계획서 조회 권한이 없습니다.')

def output(row):
    return with_correction_token({k:getattr(row,k) for k in ('id','item_id','document_no','revision_code','status','version',
            'flow_revision_id','flow_version','previous_revision_id')} | {
        'header':json.loads(row.header_json),'rows':json.loads(row.rows_json),'flow':json.loads(row.flow_snapshot_json)})


class InspectionLinkPayload(BaseModel):
    flow_step_id: int = Field(gt=0)
    category: str = Field(max_length=30)
    process_code: str | None = Field(default=None, max_length=50)


class InspectionLinksPayload(BaseModel):
    version: int = Field(gt=0)
    links: list[InspectionLinkPayload] = Field(default_factory=list, max_length=500)


@router.get('/revisions/{id}/inspection-links')
def inspection_links(id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    existing(db, id)
    links = db.query(ControlPlanInspectionLink).filter(ControlPlanInspectionLink.plan_id == id).all()
    processes = db.query(ProcessModel).filter(ProcessModel.is_active == 'Y', ProcessModel.process_code.in_(INTERNAL_CODES)).order_by(ProcessModel.sort_order).all()
    return {'links': [{'flow_step_id': x.flow_step_id, 'category': x.category, 'process_code': x.process_code} for x in links],
            'processes': [{'code': x.process_code, 'name': x.process_name} for x in processes]}


@router.put('/revisions/{id}/inspection-links')
def save_inspection_links(id: int, payload: InspectionLinksPayload, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user, True)
    row = existing(db, id, payload.version)
    if row.status not in {'DRAFT', 'CURRENT'}:
        raise HTTPException(409, '이전 관리계획서의 검사 연결은 변경할 수 없습니다.')
    lock_item(db, row.item_id)
    require_finished_item(db, db.get(ItemMasterModel, row.item_id))
    steps = {s['id'] for s in json.loads(row.flow_snapshot_json).get('steps', [])}
    ids = [x.flow_step_id for x in payload.links]
    if len(ids) != len(set(ids)) or not set(ids).issubset(steps):
        raise HTTPException(422, '공정이 중복되었거나 관리계획서에 없는 공정입니다.')
    codes = {p.process_code for p in db.query(ProcessModel).filter(ProcessModel.is_active == 'Y', ProcessModel.process_code.in_(INTERNAL_CODES)).all()}
    for link in payload.links:
        if link.category not in CATEGORIES or (link.category == 'PROCESS' and link.process_code not in codes):
            raise HTTPException(422, '검사구분과 등록된 내부 공정을 확인해 주세요.')
        if link.category != 'PROCESS' and link.process_code:
            raise HTTPException(422, '내부 공정코드는 공정검사에만 지정할 수 있습니다.')
    db.query(ControlPlanInspectionLink).filter(ControlPlanInspectionLink.plan_id == id).delete()
    for link in payload.links:
        db.add(ControlPlanInspectionLink(plan_id=id, **link.model_dump()))
    row.version += 1
    row.updated_at = datetime.now()
    row.updated_by_id = user.id
    commit(db)
    return output(row)

def commit(db):
    try: db.commit()
    except (IntegrityError,OperationalError) as exc:
        db.rollback()
        raise HTTPException(409,'문서 개정번호가 중복되었거나 다른 사용자가 변경했습니다. 다시 조회해 주세요.') from exc

def existing(db,id,version=None):
    row=db.get(ControlPlanRevision,id)
    if not row: raise HTTPException(404,'관리계획서를 찾을 수 없습니다.')
    if version is not None:
        item=lock_item(db,row.item_id)
        require_finished_item(db,item)
        row=db.scalar(select(ControlPlanRevision).where(ControlPlanRevision.id==id).with_for_update()
                      .execution_options(populate_existing=True))
        if row.version!=version: raise HTTPException(409,'다른 사용자가 변경했습니다. 다시 조회해 주세요.')
    return row

def validate_flow(db,item_id,flow_id,version):
    flow=usable_flow(db,item_id,flow_id)
    if flow.version!=version:
        raise HTTPException(409,'공정흐름도가 변경되었습니다. 최신 공정흐름도를 조회하고 다시 불러와 주세요.')
    return flow

def assign(db,row,payload,user,frozen=False):
    if frozen:
        snapshot=json.loads(row.flow_snapshot_json)
        if payload.flow_version!=snapshot['version']:
            raise HTTPException(422,'수정 시 기존 기준 공정흐름도 버전을 유지해야 합니다.')
        order={s['id']:i for i,s in enumerate(snapshot['steps'])}
        step_names={s['id']:s['step_name'] for s in snapshot['steps']}
    else:
        flow=validate_flow(db,payload.item_id,payload.flow_revision_id,payload.flow_version)
        steps=flow_steps(db,flow.id); order={s.id:i for i,s in enumerate(steps)}
        step_names={s.id:s.step_name for s in steps}
    positions=[]
    for n,input_row in enumerate(payload.rows,1):
        if input_row.flow_step_id not in order: raise HTTPException(422,f'{n}행: 기준 공정흐름도에 없는 공정입니다.')
        positions.append(order[input_row.flow_step_id])
    if positions!=sorted(positions): raise HTTPException(422,'관리항목은 공정흐름도 순서대로 입력해 주세요.')
    header=dict(payload.header);item=db.get(ItemMasterModel,payload.item_id)
    header.update(part_no=item.part_no,part_name=item.part_name,vehicle_model=item.vehicle_model or '')
    row.header_json=json.dumps(header,ensure_ascii=False)
    row.rows_json=json.dumps([x.model_dump() | {'process_detail':step_names[x.flow_step_id]} for x in payload.rows],ensure_ascii=False)
    if not frozen:
        row.flow_revision_id=flow.id;row.flow_version=flow.version
        row.flow_snapshot_json=json.dumps(flow_dict(db,flow),ensure_ascii=False)
    row.updated_by_id=user.id;row.updated_at=datetime.now()

@router.get('/options')
def options(db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    items={x.id:x for x in selectable_finished_items(db)}
    # Historical documents remain accessible after a master item is deactivated.
    for x in db.scalars(select(ItemMasterModel).where(ItemMasterModel.id.in_(select(ControlPlanRevision.item_id)))):
        items[x.id]=x
    return [{'id':x.id,'part_no':x.part_no,'part_name':x.part_name,'vehicle_model':x.vehicle_model or '',
             'selectable':is_selectable_finished_item(db,x)} for x in sorted(items.values(),key=lambda x:x.part_no)]

@router.get('/items/{item_id}/flows')
def flows(item_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    return [flow_dict(db,x) for x in db.scalars(select(ProcessFlowRevision).where(
        ProcessFlowRevision.item_id==item_id,ProcessFlowRevision.status.in_(('CURRENT','SUPERSEDED'))
    ).order_by(ProcessFlowRevision.sequence.desc()))]

@router.get('/documents')
def documents(keyword:str='',status:str='',db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    if status and status not in {'DRAFT','CURRENT','SUPERSEDED'}:
        raise HTTPException(422,'조회 상태가 올바르지 않습니다.')
    query=select(ControlPlanRevision,ItemMasterModel,UserModel).join(
        ItemMasterModel,ItemMasterModel.id==ControlPlanRevision.item_id).outerjoin(
        UserModel,UserModel.id==ControlPlanRevision.created_by_id)
    if keyword.strip():
        value=f'%{keyword.strip()}%'
        query=query.where(ItemMasterModel.part_no.ilike(value)|ItemMasterModel.part_name.ilike(value)
                          |ControlPlanRevision.document_no.ilike(value))
    grouped={}
    for revision,item,author in db.execute(query.order_by(ItemMasterModel.part_no,ControlPlanRevision.id.desc())):
        grouped.setdefault((revision.item_id,revision.document_no),[]).append((revision,item,author))
    result=[]
    for history in grouped.values():
        matches=[entry for entry in history if not status or entry[0].status==status]
        if not matches:
            continue
        revision,item,author=matches[0]
        current=next((entry[0] for entry in history if entry[0].status=='CURRENT'),None)
        result.append({'revision_id':revision.id,'item_id':item.id,'part_no':item.part_no,
                       'part_name':item.part_name,'document_no':revision.document_no,
                       'revision_code':revision.revision_code,'status':revision.status,
                       'current_revision':current.revision_code if current else '',
                       'created_by':author.username if author else '',
                       'created_at':revision.created_at.strftime('%Y-%m-%d %H:%M:%S')})
    return result

@router.get('/revisions')
def revisions(item_id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user)
    return [output(x) for x in db.scalars(select(ControlPlanRevision).where(ControlPlanRevision.item_id==item_id)
        .order_by(ControlPlanRevision.id.desc()))]

@router.get('/revisions/{id}')
def detail(id:int,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user);return output(existing(db,id))

@router.post('/revisions')
def create(payload:Save,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);item=lock_item(db,payload.item_id);require_finished_item(db,item)
    row=ControlPlanRevision(item_id=item.id,document_no=payload.document_no,revision_code=payload.revision_code,
        created_by_id=user.id,updated_by_id=user.id,status='DRAFT',version=1)
    assign(db,row,payload,user);db.add(row);commit(db);return output(row)

@router.put('/revisions/{id}')
def save(id:int,payload:Save,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True)
    if payload.version is None: raise HTTPException(422,'문서 버전이 필요합니다.')
    row=existing(db,id,payload.version)
    if row.status not in {'DRAFT','CURRENT'}: raise HTTPException(409,'초안 또는 현재 사용 문서만 수정할 수 있습니다.')
    before=output(row) if row.status=='CURRENT' else None
    if before is not None:
        validate_correction(payload.correction_reason,payload.correction_token,before)
        if payload.flow_revision_id!=row.flow_revision_id:
            raise HTTPException(422,'수정 시 기준 공정흐름도 연결은 유지해야 합니다. 연결 변경은 개정 등록을 이용해 주세요.')
    if (row.item_id,row.document_no,row.revision_code)!=(payload.item_id,payload.document_no,payload.revision_code):
        raise HTTPException(422,'저장된 문서의 품목·문서번호·개정번호는 변경할 수 없습니다.')
    assign(db,row,payload,user,frozen=before is not None);row.version+=1
    if before is not None:
        record_correction(db,user,'control_plan_revisions',row.id,payload.correction_reason,before,output(row))
    commit(db);return output(row)

@router.post('/revisions/{id}/activate')
def activate(id:int,payload:Version,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);row=existing(db,id,payload.version)
    if row.status!='DRAFT': raise HTTPException(409,'초안만 적용할 수 있습니다.')
    flow=validate_flow(db,row.item_id,row.flow_revision_id,row.flow_version)
    data=json.loads(row.rows_json);steps=flow_steps(db,flow.id)
    if set(x['flow_step_id'] for x in data)!={s.id for s in steps}:
        raise HTTPException(422,'모든 공정에 관리항목을 입력해 주세요.')
    for n,x in enumerate(data,1):
        if not (x['product'] or x['process']) or any(not x[k] for k in ('specification','method','control_method','reaction')):
            raise HTTPException(422,f'{n}행: 관리항목·규격·확인방법·관리방안·이상 조치를 입력해 주세요.')
    current=db.scalars(select(ControlPlanRevision).where(ControlPlanRevision.item_id==row.item_id,
        ControlPlanRevision.document_no==row.document_no,ControlPlanRevision.status=='CURRENT')).all()
    for old in current: old.status='SUPERSEDED';old.version+=1
    db.flush();row.status='CURRENT';row.version+=1;row.activated_at=datetime.now();commit(db);return output(row)

@router.post('/revisions/{id}/revise')
def revise(id:int,payload:Revise,db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);old=existing(db,id,payload.version)
    if old.status not in {'CURRENT','SUPERSEDED'}: raise HTTPException(409,'적용된 문서에서 개정을 등록해 주세요.')
    row=ControlPlanRevision(item_id=old.item_id,document_no=old.document_no,revision_code=payload.revision_code,
        status='DRAFT',version=1,flow_revision_id=old.flow_revision_id,flow_version=old.flow_version,
        flow_snapshot_json=old.flow_snapshot_json,header_json=old.header_json,rows_json=old.rows_json,
        previous_revision_id=old.id,created_by_id=user.id,updated_by_id=user.id)
    db.add(row);db.flush()
    for link in db.query(ControlPlanInspectionLink).filter(ControlPlanInspectionLink.plan_id == old.id).all():
        db.add(ControlPlanInspectionLink(plan_id=row.id, flow_step_id=link.flow_step_id,
                                         category=link.category, process_code=link.process_code))
    commit(db);return output(row)

@router.post('/import-excel')
def import_excel(file:UploadFile=File(...),item_id:int=Form(...,gt=0),flow_revision_id:int=Form(...,gt=0),
    flow_version:int=Form(...,gt=0),revision_id:int|None=Form(None,gt=0),revision_version:int|None=Form(None,gt=0),
    db:Session=Depends(get_db),user=Depends(get_current_user)):
    access(user,True);item=db.get(ItemMasterModel,item_id)
    if not item: raise HTTPException(404,'품목을 찾을 수 없습니다.')
    require_finished_item(db,item)
    if revision_version is not None and revision_id is None:
        raise HTTPException(422,'초안 식별자가 필요합니다.')
    if revision_id:
        row=existing(db,revision_id)
        if row.item_id!=item_id or row.status!='DRAFT' or row.version!=revision_version:
            raise HTTPException(409,'선택한 초안이 변경되었습니다. 다시 조회해 주세요.')
    flow=validate_flow(db,item_id,flow_revision_id,flow_version)
    if not (file.filename or '').lower().endswith('.xlsx'): raise HTTPException(422,'회사 양식 .xlsx 파일을 선택해 주세요.')
    try: rows=parse_control_plan(file.file.read(MAX_BYTES+1),flow_steps(db,flow.id))
    except ControlPlanImportError as exc:
        raise HTTPException(422,{'message':str(exc),'errors':exc.errors}) from exc
    return {'rows':rows,'flow_version':flow.version,'message':'첫 시트의 하단 관리항목만 불러왔습니다. 상단·서명 이미지·인쇄 영역 밖의 메모는 가져오지 않습니다. 초안 저장을 눌러 반영해 주세요.'}
