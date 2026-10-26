from datetime import date
import math
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, selectinload
from core.database import get_db
from core.security import get_current_user, check_admin_permission, parse_user_permissions, DEV_BYPASS_AUTH
from models.models import ItemMasterModel
from models.inspection_standard import InspectionStandard
from models.quality_report import InspectionReportMaster, InspectionReportItem
from services.revision_number_service import display_revision_code
from services.quality_report_service import evaluate, report_dict, build_report_excel

router = APIRouter(tags=['Inspection reports'])
templates = Jinja2Templates(directory='templates')
templates.env.filters['revision_display'] = display_revision_code
MENU = '/quality/outbound-reports'


def menu_access(user, write=False):
    if DEV_BYPASS_AUTH or check_admin_permission(user):
        return
    access = parse_user_permissions(user).get('menu_access') or {}
    value = access.get(MENU)
    level = 'WRITE' if value is True else str(value or 'NONE').upper()
    if level not in ({'WRITE'} if write else {'READ', 'WRITE'}):
        raise HTTPException(403, '출하 성적서 메뉴 권한이 없습니다.')


class MeasurementInput(BaseModel):
    standard_item_id: int
    values: list[float | None] = Field(min_length=5, max_length=5)
    manual_judgment: str = Field(default='', pattern='^(|OK|NG)$')

    @field_validator('values')
    @classmethod
    def finite(cls, values):
        if any(v is not None and not math.isfinite(v) for v in values):
            raise ValueError('측정값은 유한한 숫자여야 합니다.')
        return values


class ReportInput(BaseModel):
    part_no: str = Field(min_length=1, max_length=100)
    lot_no: str = Field(min_length=1, max_length=100)
    inspection_date: date
    standard_id: int
    order_no: str = Field(default='', max_length=50)
    note: str = Field(default='', max_length=2000)
    items: list[MeasurementInput] = Field(min_length=1, max_length=27)


@router.get(MENU, response_class=HTMLResponse)
def page(request: Request, user=Depends(get_current_user)):
    menu_access(user)
    return templates.TemplateResponse(request=request, name='quality_outbound_reports.html', context={'user': user})


@router.get('/api/quality/outbound-reports/standards')
def standards(part_no: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    menu_access(user)
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no.strip()).first()
    if not item:
        raise HTTPException(404, '등록된 품번이 없습니다.')
    rows = db.query(InspectionStandard).options(selectinload(InspectionStandard.items)).filter(
        InspectionStandard.item_id == item.id, InspectionStandard.document_type == 'FINAL',
        InspectionStandard.status == 'CURRENT').order_by(InspectionStandard.id).all()
    return {'part_no': item.part_no, 'part_name': item.part_name, 'items': [
        {'id': s.id, 'revision': s.revision, 'management_no': s.management_no,
         'items': [{'id': i.id, 'item_name': i.inspection_item_name, 'spec_display': i.spec_text or '',
                    'spec_min': i.lower_limit, 'spec_max': i.upper_limit, 'eval_tool': i.inspection_tool or ''}
                   for i in s.items]} for s in rows]}


@router.get('/api/quality/outbound-reports')
def listing(part_no: str = '', start_date: date | None = None, end_date: date | None = None,
            limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), user=Depends(get_current_user)):
    menu_access(user)
    query = db.query(InspectionReportMaster).filter(InspectionReportMaster.report_type == 'SHIPPING')
    if part_no.strip():
        query = query.filter(InspectionReportMaster.part_no == part_no.strip())
    if start_date:
        query = query.filter(InspectionReportMaster.inspection_date >= start_date.isoformat())
    if end_date:
        query = query.filter(InspectionReportMaster.inspection_date <= end_date.isoformat())
    total = query.count()
    reports = query.options(selectinload(InspectionReportMaster.items)).order_by(InspectionReportMaster.id.desc()).limit(limit).all()
    return {'items': [report_dict(r) for r in reports], 'total': total}


@router.post('/api/quality/outbound-reports')
def save(payload: ReportInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    menu_access(user, write=True)
    standard = db.get(InspectionStandard, payload.standard_id)
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == payload.part_no.strip()).first()
    if not item or not standard or standard.item_id != item.id or standard.document_type != 'FINAL' or standard.status != 'CURRENT':
        raise HTTPException(422, '해당 품번의 현행 최종검사 기준서를 선택해 주세요.')
    by_id = {i.standard_item_id: i for i in payload.items}
    if len(by_id) != len(payload.items) or set(by_id) != {i.id for i in standard.items}:
        raise HTTPException(422, '기준서의 모든 검사항목을 중복 없이 입력해 주세요.')
    if not payload.lot_no.strip():
        raise HTTPException(422, 'LOT 번호를 입력해 주세요.')
    report = InspectionReportMaster(report_no=f'UCT-IR-{payload.inspection_date:%Y%m%d}-{uuid4().hex[:12]}',
        report_type='SHIPPING', item_id=item.id, part_no=item.part_no, part_name=item.part_name,
        lot_no=payload.lot_no.strip(), order_no=payload.order_no, inspection_date=payload.inspection_date.isoformat(),
        inspector_id=user.id, inspector_name=user.name or user.username, standard_id=standard.id,
        standard_revision=standard.revision, sample_qty=5, note=payload.note, judgment='HOLD')
    for seq, source in enumerate(standard.items, 1):
        if any(limit is not None and not math.isfinite(limit) for limit in (source.lower_limit, source.upper_limit)):
            raise HTTPException(422, '검사기준서의 규격 한계값을 확인해 주세요.')
        if source.lower_limit is not None and source.upper_limit is not None and source.lower_limit > source.upper_limit:
            raise HTTPException(422, '검사기준서의 규격 상한·하한을 확인해 주세요.')
        measurement = by_id[source.id]
        average, judgment, _ = evaluate(measurement.values, source.lower_limit, source.upper_limit, measurement.manual_judgment)
        report.items.append(InspectionReportItem(standard_item_id=source.id, item_seq=seq,
            item_category=source.inspection_group_no or '', item_name=source.inspection_item_name,
            spec_display=source.spec_text or '', spec_min=source.lower_limit, spec_max=source.upper_limit,
            eval_tool=source.inspection_tool, avg_val=average, judgment=judgment,
            **{f'val_{n}': value for n, value in enumerate(measurement.values, 1)}))
    judgments = {i.judgment for i in report.items}
    report.judgment = 'REJECT' if 'NG' in judgments else ('HOLD' if 'HOLD' in judgments else 'PASS')
    try:
        db.add(report)
        db.commit()
        db.refresh(report)
    except Exception:
        db.rollback()
        raise
    return {'success': True, 'item': report_dict(report)}


def get_report(db, report_id):
    report = db.get(InspectionReportMaster, report_id)
    if not report or report.report_type != 'SHIPPING':
        raise HTTPException(404, '성적서를 찾을 수 없습니다.')
    return report


@router.get('/api/quality/outbound-reports/{report_id}/excel')
def excel(report_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    menu_access(user)
    report = get_report(db, report_id)
    return StreamingResponse(build_report_excel(report), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{report.report_no}.xlsx"'})


@router.get('/api/quality/outbound-reports/{report_id}/print', response_class=HTMLResponse)
def print_report(request: Request, report_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    menu_access(user)
    return templates.TemplateResponse(request=request, name='quality_outbound_report_print.html',
        context={'report': report_dict(get_report(db, report_id))})
