import json
from typing import Literal
from datetime import date, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import case, or_, tuple_, update
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import check_admin_permission, get_current_user
from models.models import ItemMasterModel, ProcessModel
from models.packing_sync import ExternalPackingRecord, PackingSyncState
from models.production_sync import ExternalProductionRecord, ProductionSyncProcessMap, ProductionSyncRun, ProductionSyncItemMap, ProductionSyncState
from services.production_sync_client import SyncError, ProductionClient
from services.production_sync_credentials import CredentialError, credential_status, read_credentials, save_credentials
from services.production_sync_service import credentials_ready, now, start_manual, state, today
from services.production_sync_mapping import ItemConnections
from services.production_sync_view import external_record_view

router = APIRouter(tags=['Production synchronization'])
templates = Jinja2Templates(directory='templates')
def access(user, admin=False):
    if not check_admin_permission(user):
        raise HTTPException(403, '관리자만 외부 연동 관리에 접근할 수 있습니다.')


def local_time(value):
    return value.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S') if value else ''


@router.get('/admin/production-sync', response_class=HTMLResponse)
def page(request: Request, user=Depends(get_current_user)):
    access(user)
    return templates.TemplateResponse(request=request, name='production_external.html',
                                      context={'user': user, 'can_manage': check_admin_permission(user)})


@router.get('/production/performance/external')
def old_page(user=Depends(get_current_user)):
    access(user)
    return RedirectResponse('/admin/production-sync', status_code=303)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(default='', max_length=4096)


def validate_login(payload, db):
    username = payload.username.strip()
    if not username:
        raise HTTPException(422, '연동 아이디를 입력해 주세요.')
    try:
        password = payload.password
        if not password:
            saved_user, password = read_credentials(db)
            if saved_user != username or not password:
                raise CredentialError('아이디를 변경하거나 처음 설정할 때는 비밀번호를 입력해 주세요.')
        ProductionClient(username, password)
        return username, password
    except (CredentialError, SyncError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/api/production/external-sync/credentials')
def credentials(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    return credential_status(db)


@router.post('/api/production/external-sync/credentials/test')
def test_credentials(payload: Credentials, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    validate_login(payload, db)
    return {'message': '외부 생산 시스템 로그인에 성공했습니다.'}


@router.put('/api/production/external-sync/credentials')
def update_credentials(payload: Credentials, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    username, password = validate_login(payload, db)
    state(db)
    state(db, 'packing')
    instant = now()
    changed = db.execute(update(ProductionSyncState).where(
        ProductionSyncState.id == 1, or_(ProductionSyncState.lease_until.is_(None), ProductionSyncState.lease_until < instant)
    ).values(last_error=None))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, '동기화 실행 중에는 연동 계정을 변경할 수 없습니다.')
    packing_lock = db.execute(update(PackingSyncState).where(
        PackingSyncState.id == 1, or_(PackingSyncState.lease_until.is_(None), PackingSyncState.lease_until < instant)
    ).values(last_error=None))
    if packing_lock.rowcount != 1:
        db.rollback()
        raise HTTPException(409, '포장 동기화 실행 중에는 연동 계정을 변경할 수 없습니다.')
    try:
        save_credentials(db, username, password)
    except CredentialError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    return {'message': '로그인을 확인하고 연동 계정을 저장했습니다. 서버 재시작 없이 적용됩니다.'}


@router.get('/api/production/external-sync/settings')
def settings(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    row = state(db)
    return {'enabled': row.enabled, 'interval_minutes': row.interval_minutes, 'lookback_days': row.lookback_days,
            'credentials_ready': credentials_ready(db), 'running': bool(row.lease_until and row.lease_until > now()),
            'initial_completed_at': local_time(row.initial_completed_at), 'initial_start_date': today().replace(month=1, day=1).isoformat(),
            'last_run_at': local_time(row.last_run_at), 'last_success_at': local_time(row.last_success_at),
            'next_run_at': local_time(row.next_run_at), 'last_error': row.last_error or '',
            'counts': json.loads(row.last_counts_json), 'source': 'http://14.63.172.132:8081'}


class Settings(BaseModel):
    enabled: bool
    interval_minutes: int = Field(60, ge=5, le=1440)
    lookback_days: int = Field(7, ge=1, le=90)


@router.put('/api/production/external-sync/settings')
def save_settings(payload: Settings, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user, True)
    if payload.enabled and not credentials_ready(db):
        raise HTTPException(422, '먼저 연동 계정 설정에서 아이디와 비밀번호를 저장해 주세요.')
    row = state(db)
    row.enabled = payload.enabled; row.interval_minutes = payload.interval_minutes; row.lookback_days = payload.lookback_days
    row.next_run_at = now() if payload.enabled else None
    db.commit()
    return {'message': '연동 설정을 저장했습니다.'}


class Run(BaseModel):
    start_date: date
    end_date: date


@router.post('/api/production/external-sync/run', status_code=202)
def run(payload: Run, user=Depends(get_current_user)):
    access(user, True)
    if payload.start_date > payload.end_date or (payload.end_date - payload.start_date).days >= 366 or payload.end_date > today():
        raise HTTPException(422, '오늘까지의 기간 중 최대 366일을 선택해 주세요.')
    try:
        run_id = start_manual(payload.start_date, payload.end_date)
    except SyncError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'run_id': run_id, 'message': '생산실적 동기화를 시작했습니다.'}


@router.get('/api/production/external-sync/runs')
def runs(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    return [{'id': row.id, 'started_at': local_time(row.started_at), 'finished_at': local_time(row.finished_at),
             'start_date': row.start_date, 'end_date': row.end_date, 'trigger': row.trigger, 'status': row.status,
             'counts': json.loads(row.counts_json), 'error': row.error or ''}
            for row in db.query(ProductionSyncRun).order_by(ProductionSyncRun.id.desc()).limit(30)]


@router.get('/api/production/external-sync/process-maps')
def process_maps(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    maps = {row.source_name: row for row in db.query(ProductionSyncProcessMap)}
    names = {row[0] for row in db.query(ExternalProductionRecord.process_name).distinct()}
    return {'mappings': [{'source_name': name, 'process_code': maps[name].process_code if name in maps else '', 'performance_type': maps[name].performance_type if name in maps else ''} for name in sorted(names | maps.keys())],
            'processes': [{'code': p.process_code, 'name': p.process_name}
                          for p in db.query(ProcessModel).filter(ProcessModel.is_active == 'Y').order_by(ProcessModel.sort_order)]}


class Mapping(BaseModel):
    source_name: str = Field(min_length=1, max_length=200)
    process_code: str = Field(max_length=100)
    performance_type: Literal['', 'MACHINING', 'ASSEMBLY'] = ''


@router.put('/api/production/external-sync/process-maps')
def save_mapping(payload: Mapping, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user, True)
    if payload.performance_type and not payload.process_code:
        raise HTTPException(422, '가공/조립 구분을 지정하려면 MES 공정도 선택해 주세요.')
    if payload.process_code and not db.query(ProcessModel).filter(ProcessModel.process_code == payload.process_code, ProcessModel.is_active == 'Y').first():
        raise HTTPException(422, '사용 중인 MES 공정을 선택해 주세요.')
    row = db.query(ProductionSyncProcessMap).filter(ProductionSyncProcessMap.source_name == payload.source_name).first()
    if not payload.process_code:
        if row:
            db.delete(row)
    elif row:
        row.process_code = payload.process_code
        row.performance_type = payload.performance_type
    else:
        db.add(ProductionSyncProcessMap(source_name=payload.source_name, process_code=payload.process_code, performance_type=payload.performance_type))
    db.commit()
    return {'message': '공정 연결을 저장했습니다.'}


@router.get('/api/production/external-sync/records')
def records(start_date: date | None = None, end_date: date | None = None,
            keyword: str = Query('', max_length=200), page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, '시작일과 종료일을 확인해 주세요.')
    query = db.query(ExternalProductionRecord)
    connections = ItemConnections(db)
    if start_date:
        query = query.filter(ExternalProductionRecord.work_date >= start_date.isoformat())
    if end_date:
        query = query.filter(ExternalProductionRecord.work_date <= end_date.isoformat())
    if keyword.strip():
        pattern = '%' + keyword.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        pairs = query.with_entities(ExternalProductionRecord.part_no, ExternalProductionRecord.process_name).distinct().all()
        matching_pairs = []
        for part, process in pairs:
            item, _ = connections.resolve(part, process)
            if item and keyword.strip().casefold() in item.part_no.casefold():
                matching_pairs.append((part, process))
        mapped_match = or_(*[tuple_(ExternalProductionRecord.part_no, ExternalProductionRecord.process_name).in_(
            matching_pairs[i:i + 400]) for i in range(0, len(matching_pairs), 400)]) if matching_pairs else False
        query = query.filter(or_(ExternalProductionRecord.part_no.ilike(pattern, escape='\\'),
                                 ExternalProductionRecord.lot_no.ilike(pattern, escape='\\'), mapped_match))
    total = query.count()
    rows = query.order_by(ExternalProductionRecord.work_date.desc(), ExternalProductionRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    output = [external_record_view(r, connections) for r in rows]
    return {'rows': output, 'total': total, 'page': page, 'page_size': page_size}


@router.get('/api/production/external-sync/item-maps')
def item_maps(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    pairs = set(db.query(ExternalProductionRecord.part_no, ExternalProductionRecord.process_name).distinct().all())
    pairs.update((part, '포장') for (part,) in db.query(ExternalPackingRecord.part_no).distinct())
    connections = ItemConnections(db)
    result = []
    for part, process in sorted(pairs):
        item, connection_type = connections.resolve(part, process)
        result.append({'source_part_no': part, 'source_process': process, 'item_id': item.id if item else None,
                       'part_no': item.part_no if item else '', 'part_name': item.part_name if item else '',
                       'linked': bool(item), 'explicit': connection_type == 'MANUAL', 'connection_type': connection_type})
    return result


@router.get('/api/production/external-sync/item-candidates')
def item_candidates(source_process: str = Query('', max_length=200), source_part_no: str = Query('', max_length=200), keyword: str = Query('', max_length=200),
                    page: int = Query(1, ge=1), db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    connections = ItemConnections(db)
    code, suffix = connections.process_code(source_process), connections.suffix(source_process)
    bom_candidates, _ = connections.bom_candidates(source_part_no, suffix)
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == 'Y')
    if keyword.strip():
        pattern = '%' + keyword.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.filter(or_(ItemMasterModel.part_no.ilike(pattern, escape='\\'), ItemMasterModel.part_name.ilike(pattern, escape='\\')))
    total = query.count()
    preferred = ItemMasterModel.production_loc == code if code else False
    if suffix and suffix != '?':
        preferred = or_(preferred, ItemMasterModel.part_no.ilike('%' + suffix))
    elif suffix == '':
        preferred = or_(preferred, ItemMasterModel.material_type.in_(['FINISHED', '완제품']), ItemMasterModel.account_type == '완제품')
    return {'rows': [{'id': r.id, 'part_no': r.part_no, 'part_name': r.part_name}
                     for r in query.order_by(case((ItemMasterModel.id.in_([item.id for item in bom_candidates]), 0),
                                                   (preferred, 1), else_=2), ItemMasterModel.part_no).offset((page - 1) * 50).limit(50)],
            'total': total, 'page': page, 'message': '사용 중인 MES 품목을 모두 조회합니다. BOM의 해당 공정 품목을 먼저 표시하며 다른 품번도 직접 연결할 수 있습니다.'}


class ItemMapping(BaseModel):
    source_part_no: str = Field(min_length=1, max_length=200)
    source_process: str = Field(min_length=1, max_length=200)
    item_id: int | None = Field(None, gt=0)


@router.put('/api/production/external-sync/item-maps')
def save_item_map(payload: ItemMapping, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user, True)
    if payload.item_id:
        item = db.get(ItemMasterModel, payload.item_id)
        if not item or item.is_active != 'Y':
            raise HTTPException(422, '사용 중인 MES 품목을 선택해 주세요.')
    row = db.query(ProductionSyncItemMap).filter(ProductionSyncItemMap.source_part_no == payload.source_part_no,
                                               ProductionSyncItemMap.source_process_name == payload.source_process).first()
    if payload.item_id is None:
        if row:
            db.delete(row)
    elif row:
        row.item_id = payload.item_id
    else:
        db.add(ProductionSyncItemMap(source_part_no=payload.source_part_no, source_process_name=payload.source_process, item_id=payload.item_id))
    db.commit()
    return {'message': '품번 연결을 저장했습니다. 기존에 가져온 실적에도 적용됩니다.'}
