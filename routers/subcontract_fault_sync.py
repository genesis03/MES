import json
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from models.subcontract_fault_sync import ExternalSubcontractFault, SubcontractFaultSyncRun, SubcontractFaultFieldMap
from routers.production_sync import Settings, Run, access, local_time, templates
from services import production_sync_service as sync
from services.production_sync_client import SyncError
from services.subcontract_fault_client import FAULT_FIELDS, QUANTITY_FIELDS
from services.subcontract_fault_view import field_settings, record_view
from services.production_sync_mapping import ItemConnections
from services.defect_report_format import report_types

router = APIRouter(tags=['External subcontract defects'])
BASE = '/api/subcontract/fault-sync'


@router.get('/admin/subcontract-fault-sync', response_class=HTMLResponse)
def page(request: Request, user=Depends(get_current_user)):
    access(user)
    return templates.TemplateResponse(request=request, name='subcontract_fault_external.html', context={'user': user, 'active_sync':'subcontract'})


@router.get(BASE + '/settings')
def settings(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    row = sync.state(db, 'subcontract')
    return {'enabled': row.enabled, 'interval_minutes': row.interval_minutes, 'lookback_days': row.lookback_days,
            'running': bool(row.lease_until and row.lease_until > sync.now()), 'credentials_ready': sync.credentials_ready(db),
            'last_success_at': local_time(row.last_success_at), 'last_error': row.last_error or '',
            'next_run_at': local_time(row.next_run_at)}


@router.put(BASE + '/settings')
def save_settings(payload: Settings, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    if payload.enabled and not sync.credentials_ready(db):
        raise HTTPException(422, '외부 연동 생산실적에서 연동 계정과 접속키를 먼저 설정해 주세요.')
    row = sync.state(db, 'subcontract')
    row.enabled, row.interval_minutes, row.lookback_days = payload.enabled, payload.interval_minutes, payload.lookback_days
    row.next_run_at = sync.now() if payload.enabled else None
    db.commit()
    return {'message': '외주불량 자동 동기화 설정을 저장했습니다.'}


@router.post(BASE + '/run', status_code=202)
def run(payload: Run, user=Depends(get_current_user)):
    access(user)
    if payload.start_date > payload.end_date or (payload.end_date - payload.start_date).days >= 366 or payload.end_date > sync.today():
        raise HTTPException(422, '오늘까지의 기간 중 최대 366일을 선택해 주세요.')
    try:
        run_id = sync.start_manual(payload.start_date, payload.end_date, 'subcontract')
    except SyncError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'run_id': run_id, 'message': '외주불량 가져오기를 시작했습니다.'}


@router.get(BASE + '/runs')
def runs(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    return [{'started_at': local_time(r.started_at), 'start_date': r.start_date, 'end_date': r.end_date,
             'trigger': r.trigger, 'status': r.status, 'counts': json.loads(r.counts_json), 'error': r.error or ''}
            for r in db.query(SubcontractFaultSyncRun).order_by(SubcontractFaultSyncRun.id.desc()).limit(30)]


@router.get(BASE + '/records')
def records(start_date: date | None = None, end_date: date | None = None, keyword: str = Query('', max_length=200),
            page: int = Query(1, ge=1), db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, '조회 시작일과 종료일을 확인해 주세요.')
    query = db.query(ExternalSubcontractFault)
    if start_date:
        query = query.filter(ExternalSubcontractFault.confirm_date >= start_date.isoformat())
    if end_date:
        query = query.filter(ExternalSubcontractFault.confirm_date <= end_date.isoformat())
    if keyword.strip():
        term = keyword.strip()
        query = query.filter(ExternalSubcontractFault.part_no.contains(term, autoescape=True)
                             | ExternalSubcontractFault.lot_no.contains(term, autoescape=True)
                             | ExternalSubcontractFault.inbound_no.contains(term, autoescape=True))
    total = query.count()
    mapping, columns, connections = field_settings(db), report_types(db), ItemConnections(db)
    values = query.order_by(ExternalSubcontractFault.confirm_date.desc(), ExternalSubcontractFault.id.desc()).offset((page-1)*20).limit(20)
    return {'rows': [record_view(r, connections, mapping, columns) for r in values], 'total': total, 'page': page, 'page_size': 20}


class FieldMapping(BaseModel):
    defects: dict[str, str] = Field(default_factory=dict, max_length=13)
    inbound_field: str = Field(default='', max_length=40)
    good_field: str = Field(default='', max_length=40)
    partner_field: str = Field(default='', max_length=40)


@router.get(BASE + '/mapping')
def mapping(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    return {'settings': field_settings(db), 'defect_types': report_types(db), 'source_fields': FAULT_FIELDS,
            'quantity_fields': QUANTITY_FIELDS}


@router.put(BASE + '/mapping')
def save_mapping(payload: FieldMapping, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    codes = {t['code'] for t in report_types(db)}
    if (any(k not in FAULT_FIELDS or v not in codes | {''} for k,v in payload.defects.items())
        or payload.inbound_field not in (*QUANTITY_FIELDS, '')
        or payload.good_field not in (*QUANTITY_FIELDS, '', 'INBOUND_MINUS_DEFECT')
        or payload.partner_field not in ('', 'APPLY_COMP_NM', 'ORDER_COMP_NM')
        or payload.good_field == 'INBOUND_MINUS_DEFECT' and not payload.inbound_field):
        raise HTTPException(422, '원본 항목과 내부 연결 항목을 확인해 주세요.')
    row = db.get(SubcontractFaultFieldMap, 1)
    if row is None:
        row = SubcontractFaultFieldMap(id=1)
        db.add(row)
    row.settings_json = json.dumps(payload.model_dump() | {'mapping_version':2}, ensure_ascii=False)
    db.commit()
    return {'message': '외주불량 항목 연결을 저장했습니다. 기존 수신 자료에도 바로 적용됩니다.'}
