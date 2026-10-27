"""Admin run history; uses the existing shared credentials and scheduler."""
import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user
from models.shipment_sync import ShipmentSyncRun
from routers.production_sync import access, local_time

router = APIRouter(prefix='/api/shipping/external-sync', tags=['Shipment synchronization'])


@router.get('/runs')
def runs(db: Session = Depends(get_db), user=Depends(get_current_user)):
    access(user)
    return [dict(started_at=local_time(r.started_at), start_date=r.start_date, end_date=r.end_date,
        trigger=r.trigger, status=r.status, counts=json.loads(r.counts_json), error=r.error or '')
        for r in db.query(ShipmentSyncRun).order_by(ShipmentSyncRun.id.desc()).limit(30)]
