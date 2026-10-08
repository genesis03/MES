import json
from typing import Literal
from datetime import date, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import check_admin_permission, get_current_user, parse_user_permissions
from models.models import ItemMasterModel, ProcessModel, ItemBomModel
from models.production_sync import ExternalProductionRecord, ProductionSyncProcessMap, ProductionSyncRun, ProductionSyncItemMap
from services.production_sync_client import SyncError
from services.production_sync_service import credentials_ready, now, start_manual, state, today

router = APIRouter(tags=['Production synchronization'])
templates = Jinja2Templates(directory='templates')
MENU = '/production/performance/status'


def access(user, admin=False):
    if check_admin_permission(user):
        return
    if admin:
        raise HTTPException(403, '관리자만 연동 설정과 즉시 동기화를 실행할 수 있습니다.')
    permissions = parse_user_permissions(user).get('menu_access')
    if permissions:
        matching = sorted((p for p in permissions if MENU == p or MENU.startswith(p.rstrip('/') + '/')), key=len, reverse=True)
        level = permissions.get(matching[0]) if matching else None
        if level is not True and str(level).upper() not in {'READ', 'WRITE'}:
            raise HTTPException(403, '생산 실적 현황 조회 권한이 없습니다.')


def local_time(value):
    return value.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S') if value else ''


@router.get('/production/performance/external', response_class=HTMLResponse)
def page(request: Request, user=Depends(get_current_user)):
    access(user)
    return templates.TemplateResponse(request=request, name='production_external.html',
                                      context={'user': user, 'can_manage': check_admin_permission(user)})


@router.get('/api/production/external-sync/settings')
def settings(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    row = state(db)
    return {'enabled': row.enabled, 'interval_minutes': row.interval_minutes, 'lookback_days': row.lookback_days,
            'credentials_ready': credentials_ready(), 'running': bool(row.lease_until and row.lease_until > now()),
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
    if payload.enabled and not credentials_ready():
        raise HTTPException(422, '먼저 서버에 외부 시스템의 연동 계정을 설정해 주세요.')
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
    if start_date:
        query = query.filter(ExternalProductionRecord.work_date >= start_date.isoformat())
    if end_date:
        query = query.filter(ExternalProductionRecord.work_date <= end_date.isoformat())
    if keyword.strip():
        pattern = '%' + keyword.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        mapped_match = db.query(ProductionSyncItemMap.id).join(ItemMasterModel, ProductionSyncItemMap.item_id == ItemMasterModel.id).filter(
            ProductionSyncItemMap.source_part_no == ExternalProductionRecord.part_no,
            ProductionSyncItemMap.source_process_name == ExternalProductionRecord.process_name,
            ItemMasterModel.part_no.ilike(pattern, escape='\\')).exists()
        query = query.filter(or_(ExternalProductionRecord.part_no.ilike(pattern, escape='\\'),
                                 ExternalProductionRecord.lot_no.ilike(pattern, escape='\\'), mapped_match))
    total = query.count()
    rows = query.order_by(ExternalProductionRecord.work_date.desc(), ExternalProductionRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    parts = {r.part_no for r in rows}
    item_maps = {(m.source_part_no, m.source_process_name): m.item_id for m in db.query(ProductionSyncItemMap).filter(ProductionSyncItemMap.source_part_no.in_(parts))}
    item_rows = db.query(ItemMasterModel).filter(or_(ItemMasterModel.part_no.in_(parts), ItemMasterModel.id.in_(list(item_maps.values())))).all()
    items = {r.part_no: r for r in item_rows}
    items_by_id = {r.id: r for r in item_rows}
    processes = {r.process_code: r.process_name for r in db.query(ProcessModel).filter(ProcessModel.is_active == 'Y')}
    by_name = {}
    for code, name in processes.items():
        by_name.setdefault(name, []).append(code)
    map_rows = {r.source_name: r for r in db.query(ProductionSyncProcessMap)}
    maps = {name: mapping.process_code for name, mapping in map_rows.items()}
    output = []
    for r in rows:
        mapped_id = item_maps.get((r.part_no, r.process_name))
        item = items_by_id.get(mapped_id) if mapped_id else items.get(r.part_no)
        raw = json.loads(r.raw_json)
        candidates = by_name.get(r.process_name, [])
        code = maps.get(r.process_name) or (candidates[0] if len(candidates) == 1 else '')
        notes = []
        if not item or item.is_active != 'Y':
            notes.append('품번 연결 확인')
        if code not in processes:
            notes.append('공정 연결 확인')
        setup_qty = raw.get('F10', '')
        if setup_qty == '':
            notes.append('SET-UP 수량 누락')
        elif Decimal(r.lot_qty) != Decimal(r.job_qty) + Decimal(r.fault_qty) + Decimal(setup_qty):
            notes.append('전체수량과 양품·불량·SET-UP 합계 확인')
        output.append({'id': r.id, 'work_date': r.work_date, 'part_no': r.part_no,
                       'part_name': item.part_name if item else raw.get('PRODUCT_NM', ''),
                       'item_id': item.id if item else None, 'mes_part_no': item.part_no if item else '',
                       'source_process': r.process_name, 'process_code': code,
                       'performance_type': map_rows[r.process_name].performance_type if r.process_name in map_rows else '',
                       'process_name': processes.get(code, ''), 'job_no': r.job_no, 'lot_no': r.lot_no,
                       'started_at': r.started_at or '', 'ended_at': r.ended_at or '',
                       'job_qty': r.job_qty, 'lot_qty': r.lot_qty, 'fault_qty': r.fault_qty,
                       'good_qty': r.job_qty, 'setup_qty': setup_qty, 'notes': notes, 'changed_at': local_time(r.changed_at)})
    return {'rows': output, 'total': total, 'page': page, 'page_size': page_size}


@router.get('/api/production/external-sync/item-maps')
def item_maps(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    pairs = db.query(ExternalProductionRecord.part_no, ExternalProductionRecord.process_name).distinct().all()
    maps = {(r.source_part_no, r.source_process_name): r.item_id for r in db.query(ProductionSyncItemMap)}
    items = {r.id: r for r in db.query(ItemMasterModel)}
    by_part = {r.part_no: r for r in items.values()}
    result = []
    for part, process in sorted(pairs):
        mapped_id = maps.get((part, process))
        item = items.get(mapped_id) if mapped_id else by_part.get(part)
        result.append({'source_part_no': part, 'source_process': process, 'item_id': item.id if item else None,
                       'part_no': item.part_no if item else '', 'part_name': item.part_name if item else '',
                       'linked': bool(item and item.is_active == 'Y'), 'explicit': mapped_id is not None})
    return result


@router.get('/api/production/external-sync/item-candidates')
def item_candidates(source_process: str = Query('', max_length=200), keyword: str = Query('', max_length=200),
                    page: int = Query(1, ge=1), db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    process_map = db.query(ProductionSyncProcessMap).filter(ProductionSyncProcessMap.source_name == source_process).first()
    candidates = db.query(ProcessModel).filter(ProcessModel.process_name == source_process, ProcessModel.is_active == 'Y').all()
    code = process_map.process_code if process_map else candidates[0].process_code if len(candidates) == 1 else ''
    if not code:
        return {'rows': [], 'total': 0, 'page': page, 'message': '먼저 원본 공정을 MES 공정에 연결해 주세요.'}
    bom_items = db.query(ItemBomModel.child_item_id).filter(ItemBomModel.process_code == code)
    bom_parts = db.query(ItemBomModel.child_part_no).filter(ItemBomModel.process_code == code)
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == 'Y', or_(
        ItemMasterModel.production_loc == code, ItemMasterModel.id.in_(bom_items), ItemMasterModel.part_no.in_(bom_parts)))
    if keyword.strip():
        pattern = '%' + keyword.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        query = query.filter(or_(ItemMasterModel.part_no.ilike(pattern, escape='\\'), ItemMasterModel.part_name.ilike(pattern, escape='\\')))
    total = query.count()
    return {'rows': [{'id': r.id, 'part_no': r.part_no, 'part_name': r.part_name}
                     for r in query.order_by(ItemMasterModel.part_no).offset((page - 1) * 50).limit(50)],
            'total': total, 'page': page, 'message': '해당 공정에 등록된 품목과 BOM 품목을 표시합니다.'}


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
        process_map = db.query(ProductionSyncProcessMap).filter(ProductionSyncProcessMap.source_name == payload.source_process).first()
        processes = db.query(ProcessModel).filter(ProcessModel.process_name == payload.source_process, ProcessModel.is_active == 'Y').all()
        code = process_map.process_code if process_map else processes[0].process_code if len(processes) == 1 else ''
        in_bom = db.query(ItemBomModel).filter(ItemBomModel.process_code == code, or_(
            ItemBomModel.child_item_id == item.id, ItemBomModel.child_part_no == item.part_no)).first() if code else None
        if not code or item.production_loc != code and not in_bom:
            raise HTTPException(422, '선택한 품목이 해당 공정 또는 BOM에 등록되어 있지 않습니다.')
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
