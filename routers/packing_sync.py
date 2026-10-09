import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.packing_sync import PackingSyncRun, ExternalPackingRecord
from routers.production_sync import Settings, Run, access, local_time, templates
from services import production_sync_service as sync
from services.production_sync_client import SyncError
from services.production_sync_mapping import ItemConnections
from services.packing_sync_view import packing_record_view

router = APIRouter(tags=['Packing synchronization'])
BASE = '/api/packing/external-sync'


@router.get('/admin/packing-sync', response_class=HTMLResponse)
def page(request: Request, user=Depends(get_current_user)):
    access(user)
    return templates.TemplateResponse(request=request, name='packing_external.html', context={'user': user})


@router.get(BASE + '/settings')
def settings(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    row = sync.state(db, 'packing')
    return {'enabled': row.enabled, 'interval_minutes': row.interval_minutes, 'lookback_days': row.lookback_days,
            'credentials_ready': sync.credentials_ready(db), 'running': bool(row.lease_until and row.lease_until > sync.now()),
            'initial_completed_at': local_time(row.initial_completed_at),
            'initial_start_date': sync.today().replace(month=1, day=1).isoformat(),
            'last_success_at': local_time(row.last_success_at), 'next_run_at': local_time(row.next_run_at),
            'last_error': row.last_error or ''}


@router.put(BASE + '/settings')
def save_settings(payload: Settings, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    if payload.enabled and not sync.credentials_ready(db):
        raise HTTPException(422, '외부 연동 생산실적에서 연동 계정을 먼저 저장해 주세요.')
    row = sync.state(db, 'packing')
    row.enabled = payload.enabled
    row.interval_minutes = payload.interval_minutes
    row.lookback_days = payload.lookback_days
    row.next_run_at = sync.now() if payload.enabled else None
    db.commit()
    return {'message': '포장 자동 동기화 설정을 저장했습니다.'}


@router.post(BASE + '/run', status_code=202)
def run(payload: Run, user=Depends(get_current_user)):
    access(user)
    if payload.start_date > payload.end_date or (payload.end_date - payload.start_date).days >= 366 or payload.end_date > sync.today():
        raise HTTPException(422, '오늘까지의 기간 중 최대 366일을 선택해 주세요.')
    try:
        run_id = sync.start_manual(payload.start_date, payload.end_date, 'packing')
    except SyncError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'run_id': run_id, 'message': '포장 현황 동기화를 시작했습니다.'}


@router.get(BASE + '/runs')
def runs(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    return [{'started_at': local_time(r.started_at), 'start_date': r.start_date, 'end_date': r.end_date,
             'trigger': r.trigger, 'status': r.status, 'counts': json.loads(r.counts_json), 'error': r.error or ''}
            for r in db.query(PackingSyncRun).order_by(PackingSyncRun.id.desc()).limit(30)]


@router.get(BASE + '/records')
def records(start_date: date | None = None, end_date: date | None = None,
            keyword: str = Query('', max_length=200), page: int = Query(1, ge=1),
            db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, '시작일과 종료일을 확인해 주세요.')
    query = db.query(ExternalPackingRecord)
    if start_date:
        query = query.filter(ExternalPackingRecord.packing_date >= start_date.isoformat())
    if end_date:
        query = query.filter(ExternalPackingRecord.packing_date <= end_date.isoformat())
    connections = ItemConnections(db)
    rows = [packing_record_view(r, connections) for r in query.order_by(
        ExternalPackingRecord.packing_date.desc(), ExternalPackingRecord.id.desc())]
    term = keyword.strip().casefold()
    if term:
        rows = [r for r in rows if any(term in r[k].casefold() for k in ('part_no', 'source_part_no', 'lot_no', 'part_name'))]
    return {'rows': rows[(page - 1) * 50:page * 50], 'total': len(rows), 'page': page}
