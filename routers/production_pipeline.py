from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from core.database import get_db
from core.security import get_current_user, check_admin_permission, parse_user_permissions, DEV_BYPASS_AUTH
from services.production_pipeline_service import pipeline_data

router = APIRouter(tags=['Production pipeline'])
templates = Jinja2Templates(directory='templates')


def check_access(user):
    if DEV_BYPASS_AUTH or check_admin_permission(user):
        return
    access = parse_user_permissions(user).get('menu_access') or {}
    value = access.get('/production/pipeline-dashboard')
    if value is not True and str(value or '').upper() not in {'READ', 'WRITE'}:
        raise HTTPException(403, '통합 공정 현황 메뉴 권한이 없습니다.')


@router.get('/production/pipeline-dashboard', response_class=HTMLResponse)
def page(request: Request, user=Depends(get_current_user)):
    check_access(user)
    return templates.TemplateResponse(request=request, name='production_pipeline_dashboard.html', context={'user': user})


@router.get('/api/production/pipeline-data')
def data(part_no: str = Query('', max_length=100), db: Session = Depends(get_db), user=Depends(get_current_user)):
    check_access(user)
    return pipeline_data(db, part_no, user)
