"""Common controls over independent production, subcontract and packing imports."""
import threading
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from routers.production_sync import Settings, Run, access, local_time
from services import production_sync_service as sync
from services.production_sync_client import SyncError

router = APIRouter(prefix='/api/admin/external-sync', tags=['External synchronization'])
KINDS = ('production', 'subcontract', 'packing', 'shipment')
NAMES = dict(zip(KINDS, ('생산실적','외주불량','포장현황','출고이력')))


class CommonSettings(Settings):
    targets: list[Literal['production','subcontract','packing','shipment']] = Field(min_length=1, max_length=4)


class GroupRun(Run):
    targets: list[Literal['production','subcontract','packing','shipment']] = Field(min_length=1, max_length=4)


@router.get('/settings')
def settings(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    states = []
    for kind in KINDS:
        row = sync.state(db,kind)
        states.append({'kind':kind,'name':NAMES[kind],'enabled':row.enabled,
            'interval_minutes':row.interval_minutes, 'lookback_days':row.lookback_days,
            'running':bool(row.lease_until and row.lease_until > sync.now()),
            'last_success_at':local_time(row.last_success_at),'next_run_at':local_time(row.next_run_at),
            'last_error':row.last_error or ''})
    active = [s for s in states if s['enabled']] or states
    def common(key):
        values = {s[key] for s in active}
        return values.pop() if len(values) == 1 else None
    return {'states':states,'credentials_ready':sync.credentials_ready(db),
            'interval_minutes':common('interval_minutes'),'lookback_days':common('lookback_days')}


@router.put('/settings')
def save_settings(payload: CommonSettings, db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    if payload.enabled and not sync.credentials_ready(db):
        raise HTTPException(422,'연동 계정을 먼저 저장해 주세요.')
    # Ensure singleton rows exist before applying the settings in one transaction.
    rows = {kind:sync.state(db,kind) for kind in KINDS}
    for kind, row in rows.items():
        row.enabled = payload.enabled and kind in payload.targets
        if kind in payload.targets:
            row.interval_minutes, row.lookback_days = payload.interval_minutes, payload.lookback_days
        row.next_run_at = sync.now() if row.enabled else None
    db.commit()
    return {'message':'공통 동기화 설정을 저장했습니다.'}


def execute_group(start, end, leases):
    stop = threading.Event()
    def heartbeat():
        from sqlalchemy import update
        from datetime import timedelta
        while not stop.wait(30):
            with sync.SessionLocal() as db:
                for kind, token, _ in leases:
                    State, _, _, _ = sync.backend(kind)
                    db.execute(update(State).where(State.id==1,State.lease_token==token).values(
                        lease_until=sync.now()+timedelta(minutes=10)))
                db.commit()
    threading.Thread(target=heartbeat,daemon=True).start()
    try:
        _execute_group(start,end,leases)
    finally:
        stop.set()


def _execute_group(start, end, leases):
    for kind, token, run_id in leases:
        # Earlier targets can take longer than a lease. Renew the queued lease
        # immediately before starting, without replacing another worker's token.
        State, RunModel, _, _ = sync.backend(kind)
        from sqlalchemy import update
        from datetime import timedelta
        with sync.SessionLocal() as db:
            changed = db.execute(update(State).where(State.id==1, State.lease_token==token).values(
                lease_until=sync.now()+timedelta(minutes=10)))
            if changed.rowcount != 1:
                db.rollback()
                continue
            run = db.get(RunModel, run_id)
            if run is None:
                db.rollback()
                continue
            run_start, run_end = date.fromisoformat(run.start_date), date.fromisoformat(run.end_date)
            db.commit()
        try:
            sync.execute(run_start,run_end,token,run_id,kind)
        except Exception:
            # Normally execute records errors itself; do not stop later targets.
            with sync.SessionLocal() as db:
                run = db.get(RunModel,run_id)
                if run and run.status=='RUNNING':
                    run.status='FAILED';run.finished_at=sync.now();run.error='통합 가져오기 실행 오류'
                db.execute(update(State).where(State.id==1,State.lease_token==token).values(lease_token=None,lease_until=None))
                db.commit()


@router.post('/run',status_code=202)
def run(payload: GroupRun, user=Depends(get_current_user)):
    access(user)
    if payload.start_date > payload.end_date or (payload.end_date-payload.start_date).days>=366:
        raise HTTPException(422,'시작일과 종료일을 확인하고 최대 366일을 선택해 주세요.')
    if payload.end_date > sync.today() and 'shipment' not in payload.targets:
        raise HTTPException(422,'출고이력 외의 대상은 오늘까지의 기간만 선택할 수 있습니다.')
    leases, results = [], []
    for kind in KINDS:
        if kind not in payload.targets:
            continue
        try:
            target_end = payload.end_date if kind == 'shipment' else min(payload.end_date, sync.today())
            if payload.start_date > target_end:
                raise SyncError('이 대상은 오늘까지의 자료만 가져올 수 있어 제외했습니다.')
            lease=sync.claim(payload.start_date,target_end,'MANUAL',kind)
            if lease is None:
                raise SyncError('이미 가져오기가 진행 중입니다.')
            leases.append((kind,*lease))
            results.append({'kind':kind,'name':NAMES[kind],'run_id':lease[1],'status':'QUEUED',
                            'start_date':payload.start_date.isoformat(),'end_date':target_end.isoformat()})
        except SyncError as exc:
            results.append({'kind':kind,'name':NAMES[kind],'status':'SKIPPED','error':str(exc)})
    if leases:
        threading.Thread(target=execute_group,args=(payload.start_date,payload.end_date,leases),daemon=True).start()
    return {'results':results}
