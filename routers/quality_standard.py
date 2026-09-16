from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel
from models.quality_standard import (
    QualityInboundStandard,
    QualityInboundStandardItem,
    QualityInspectionItemMaster,
)

router = APIRouter(tags=["Quality Standard"])
templates = Jinja2Templates(directory="templates")


class InspectionItemPayload(BaseModel):
    item_code: str = Field(max_length=30)
    item_name: str = Field(max_length=100)
    inspection_method: Optional[str] = Field(default=None, max_length=100)
    default_unit: Optional[str] = Field(default=None, max_length=20)
    data_type: str = Field(default="TEXT", max_length=20)
    sort_order: int = Field(default=1, ge=1)
    is_active: str = Field(default="Y", max_length=1)
    note: Optional[str] = None


class StandardItemPayload(BaseModel):
    inspection_item_id: int
    spec_type: str = Field(default="TEXT", max_length=20)
    spec_text: Optional[str] = Field(default=None, max_length=300)
    nominal_value: Optional[float] = None
    lower_limit: Optional[float] = None
    upper_limit: Optional[float] = None
    unit: Optional[str] = Field(default=None, max_length=20)
    sample_qty: int = Field(default=1, ge=1)
    required_yn: str = Field(default="Y", max_length=1)
    sort_order: int = Field(default=1, ge=1)
    note: Optional[str] = None


class StandardPayload(BaseModel):
    part_no: str = Field(max_length=80)
    revision: str = Field(default="Rev.00", max_length=20)
    effective_date: Optional[str] = Field(default=None, max_length=10)
    is_active: str = Field(default="Y", max_length=1)
    note: Optional[str] = None
    items: list[StandardItemPayload] = Field(default_factory=list)


DATA_TYPES = {"TEXT", "NUMBER", "PASSFAIL"}
SPEC_TYPES = {"TEXT", "RANGE", "MIN", "MAX", "TARGET", "PASSFAIL"}


def _user_name(user):
    return str(getattr(user, "name", "") or getattr(user, "username", "") or "").strip() or None


def _yn(value: str, label: str):
    result = (value or "").strip().upper()
    if result not in {"Y", "N"}:
        raise HTTPException(422, f"{label} 값이 올바르지 않습니다.")
    return result


def _item_dict(row: QualityInspectionItemMaster):
    return {
        "id": row.id,
        "item_code": row.item_code,
        "item_name": row.item_name,
        "inspection_method": row.inspection_method or "",
        "default_unit": row.default_unit or "",
        "data_type": row.data_type,
        "sort_order": row.sort_order,
        "is_active": row.is_active,
        "note": row.note or "",
    }


def _standard_dict(row: QualityInboundStandard, include_items: bool = False):
    data = {
        "id": row.id,
        "part_no": row.part_no,
        "revision": row.revision,
        "effective_date": row.effective_date or "",
        "is_active": row.is_active,
        "note": row.note or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
        "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds") if row.updated_at else "",
    }
    if include_items:
        data["items"] = [
            {
                "id": x.id,
                "inspection_item_id": x.inspection_item_id,
                "item_code": x.inspection_item.item_code if x.inspection_item else "",
                "item_name": x.inspection_item.item_name if x.inspection_item else "",
                "inspection_method": x.inspection_item.inspection_method if x.inspection_item else "",
                "data_type": x.inspection_item.data_type if x.inspection_item else "TEXT",
                "spec_type": x.spec_type,
                "spec_text": x.spec_text or "",
                "nominal_value": x.nominal_value,
                "lower_limit": x.lower_limit,
                "upper_limit": x.upper_limit,
                "unit": x.unit or "",
                "sample_qty": x.sample_qty,
                "required_yn": x.required_yn,
                "sort_order": x.sort_order,
                "note": x.note or "",
            }
            for x in row.items
        ]
    return data


def _validate_standard_items(db: Session, payload_items: list[StandardItemPayload]):
    if not payload_items:
        raise HTTPException(422, "검사항목을 1개 이상 등록해 주십시오.")
    ids = [x.inspection_item_id for x in payload_items]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "같은 검사항목을 중복 등록할 수 없습니다.")
    masters = db.query(QualityInspectionItemMaster).filter(QualityInspectionItemMaster.id.in_(ids)).all()
    master_map = {x.id: x for x in masters}
    if len(master_map) != len(ids):
        raise HTTPException(422, "존재하지 않는 검사항목이 포함되어 있습니다.")
    for item in payload_items:
        if master_map[item.inspection_item_id].is_active != "Y":
            raise HTTPException(422, f"사용 중지된 검사항목입니다. ({master_map[item.inspection_item_id].item_name})")
        spec_type = item.spec_type.strip().upper()
        if spec_type not in SPEC_TYPES:
            raise HTTPException(422, "지원하지 않는 규격 유형입니다.")
        if spec_type == "RANGE" and (item.lower_limit is None or item.upper_limit is None):
            raise HTTPException(422, "범위 규격은 하한과 상한이 필요합니다.")
        if item.lower_limit is not None and item.upper_limit is not None and item.lower_limit > item.upper_limit:
            raise HTTPException(422, "규격 하한은 상한보다 클 수 없습니다.")


@router.get("/quality/inspection-items", response_class=HTMLResponse)
def inspection_items_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="quality_inspection_items.html",
        context={"request": request, "user": current_user},
    )


@router.get("/quality/inbound-standards", response_class=HTMLResponse)
def inbound_standards_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="quality_inbound_standards.html",
        context={"request": request, "user": current_user},
    )


@router.get("/api/quality/inspection-items")
def list_inspection_items(
    keyword: Optional[str] = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(QualityInspectionItemMaster)
    if active_only:
        query = query.filter(QualityInspectionItemMaster.is_active == "Y")
    if keyword:
        value = keyword.strip()
        query = query.filter(
            QualityInspectionItemMaster.item_code.contains(value, autoescape=True)
            | QualityInspectionItemMaster.item_name.contains(value, autoescape=True)
            | QualityInspectionItemMaster.inspection_method.contains(value, autoescape=True)
        )
    rows = query.order_by(QualityInspectionItemMaster.sort_order, QualityInspectionItemMaster.item_code).all()
    return [_item_dict(x) for x in rows]


@router.post("/api/quality/inspection-items")
def create_inspection_item(
    payload: InspectionItemPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    code = payload.item_code.strip().upper()
    name = payload.item_name.strip()
    data_type = payload.data_type.strip().upper()
    if not code or not name:
        raise HTTPException(422, "검사항목 코드와 검사항목명은 필수입니다.")
    if data_type not in DATA_TYPES:
        raise HTTPException(422, "지원하지 않는 데이터 유형입니다.")
    if db.query(QualityInspectionItemMaster.id).filter(QualityInspectionItemMaster.item_code == code).first():
        raise HTTPException(409, "이미 등록된 검사항목 코드입니다.")
    row = QualityInspectionItemMaster(
        item_code=code,
        item_name=name,
        inspection_method=(payload.inspection_method or "").strip() or None,
        default_unit=(payload.default_unit or "").strip() or None,
        data_type=data_type,
        sort_order=payload.sort_order,
        is_active=_yn(payload.is_active, "사용여부"),
        note=(payload.note or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _item_dict(row)


@router.put("/api/quality/inspection-items/{item_id}")
def update_inspection_item(
    item_id: int,
    payload: InspectionItemPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(QualityInspectionItemMaster, item_id)
    if not row:
        raise HTTPException(404, "검사항목을 찾을 수 없습니다.")
    code = payload.item_code.strip().upper()
    name = payload.item_name.strip()
    data_type = payload.data_type.strip().upper()
    if data_type not in DATA_TYPES:
        raise HTTPException(422, "지원하지 않는 데이터 유형입니다.")
    duplicate = db.query(QualityInspectionItemMaster.id).filter(
        QualityInspectionItemMaster.item_code == code,
        QualityInspectionItemMaster.id != item_id,
    ).first()
    if duplicate:
        raise HTTPException(409, "이미 등록된 검사항목 코드입니다.")
    row.item_code = code
    row.item_name = name
    row.inspection_method = (payload.inspection_method or "").strip() or None
    row.default_unit = (payload.default_unit or "").strip() or None
    row.data_type = data_type
    row.sort_order = payload.sort_order
    row.is_active = _yn(payload.is_active, "사용여부")
    row.note = (payload.note or "").strip() or None
    db.commit()
    return _item_dict(row)


@router.delete("/api/quality/inspection-items/{item_id}")
def delete_inspection_item(
    item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(QualityInspectionItemMaster, item_id)
    if not row:
        raise HTTPException(404, "검사항목을 찾을 수 없습니다.")
    used = db.query(QualityInboundStandardItem.id).filter(QualityInboundStandardItem.inspection_item_id == item_id).first()
    if used:
        raise HTTPException(409, "품번별 검사기준서에서 사용 중인 항목은 삭제할 수 없습니다. 사용중지로 변경해 주십시오.")
    db.delete(row)
    db.commit()
    return {"message": "검사항목을 삭제했습니다."}


@router.get("/api/quality/inbound-standards/options")
def inbound_standard_options(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    products = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y").order_by(ItemMasterModel.part_no).all()
    items = db.query(QualityInspectionItemMaster).filter(QualityInspectionItemMaster.is_active == "Y").order_by(
        QualityInspectionItemMaster.sort_order, QualityInspectionItemMaster.item_code
    ).all()
    return {
        "products": [{"part_no": x.part_no, "part_name": x.part_name, "revision": x.revision, "unit": x.unit} for x in products],
        "inspection_items": [_item_dict(x) for x in items],
    }


@router.get("/api/quality/inbound-standards")
def list_inbound_standards(
    part_no: Optional[str] = None,
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(QualityInboundStandard)
    if part_no:
        query = query.filter(QualityInboundStandard.part_no.contains(part_no.strip(), autoescape=True))
    if active_only:
        query = query.filter(QualityInboundStandard.is_active == "Y")
    rows = query.order_by(QualityInboundStandard.part_no, QualityInboundStandard.id.desc()).all()
    return [_standard_dict(x) for x in rows]


@router.get("/api/quality/inbound-standards/{standard_id}")
def get_inbound_standard(
    standard_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(QualityInboundStandard, standard_id)
    if not row:
        raise HTTPException(404, "입고검사 기준서를 찾을 수 없습니다.")
    return _standard_dict(row, include_items=True)


def _apply_standard_items(row: QualityInboundStandard, payload_items: list[StandardItemPayload]):
    row.items.clear()
    for payload in sorted(payload_items, key=lambda x: x.sort_order):
        row.items.append(
            QualityInboundStandardItem(
                inspection_item_id=payload.inspection_item_id,
                spec_type=payload.spec_type.strip().upper(),
                spec_text=(payload.spec_text or "").strip() or None,
                nominal_value=payload.nominal_value,
                lower_limit=payload.lower_limit,
                upper_limit=payload.upper_limit,
                unit=(payload.unit or "").strip() or None,
                sample_qty=payload.sample_qty,
                required_yn=_yn(payload.required_yn, "필수여부"),
                sort_order=payload.sort_order,
                note=(payload.note or "").strip() or None,
            )
        )


def _deactivate_other_revisions(db: Session, part_no: str, exclude_id: Optional[int] = None):
    query = db.query(QualityInboundStandard).filter(
        QualityInboundStandard.part_no == part_no,
        QualityInboundStandard.is_active == "Y",
    )
    if exclude_id:
        query = query.filter(QualityInboundStandard.id != exclude_id)
    for row in query.all():
        row.is_active = "N"


@router.post("/api/quality/inbound-standards")
def create_inbound_standard(
    payload: StandardPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part_no = payload.part_no.strip()
    revision = payload.revision.strip()
    product = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no, ItemMasterModel.is_active == "Y").first()
    if not product:
        raise HTTPException(422, "사용 가능한 품번을 선택해 주십시오.")
    if not revision:
        raise HTTPException(422, "REV는 필수입니다.")
    if db.query(QualityInboundStandard.id).filter(
        QualityInboundStandard.part_no == part_no,
        QualityInboundStandard.revision == revision,
    ).first():
        raise HTTPException(409, "해당 품번의 동일 REV 기준서가 이미 존재합니다.")
    _validate_standard_items(db, payload.items)
    active = _yn(payload.is_active, "사용여부")
    if active == "Y":
        _deactivate_other_revisions(db, part_no)
    row = QualityInboundStandard(
        part_no=part_no,
        revision=revision,
        effective_date=(payload.effective_date or "").strip() or None,
        is_active=active,
        note=(payload.note or "").strip() or None,
        created_by=_user_name(current_user),
    )
    _apply_standard_items(row, payload.items)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _standard_dict(row, include_items=True)


@router.put("/api/quality/inbound-standards/{standard_id}")
def update_inbound_standard(
    standard_id: int,
    payload: StandardPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(QualityInboundStandard, standard_id)
    if not row:
        raise HTTPException(404, "입고검사 기준서를 찾을 수 없습니다.")
    part_no = payload.part_no.strip()
    revision = payload.revision.strip()
    product = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no, ItemMasterModel.is_active == "Y").first()
    if not product:
        raise HTTPException(422, "사용 가능한 품번을 선택해 주십시오.")
    duplicate = db.query(QualityInboundStandard.id).filter(
        QualityInboundStandard.part_no == part_no,
        QualityInboundStandard.revision == revision,
        QualityInboundStandard.id != standard_id,
    ).first()
    if duplicate:
        raise HTTPException(409, "해당 품번의 동일 REV 기준서가 이미 존재합니다.")
    _validate_standard_items(db, payload.items)
    active = _yn(payload.is_active, "사용여부")
    if active == "Y":
        _deactivate_other_revisions(db, part_no, exclude_id=standard_id)
    row.part_no = part_no
    row.revision = revision
    row.effective_date = (payload.effective_date or "").strip() or None
    row.is_active = active
    row.note = (payload.note or "").strip() or None
    _apply_standard_items(row, payload.items)
    db.commit()
    db.refresh(row)
    return _standard_dict(row, include_items=True)


@router.delete("/api/quality/inbound-standards/{standard_id}")
def delete_inbound_standard(
    standard_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(QualityInboundStandard, standard_id)
    if not row:
        raise HTTPException(404, "입고검사 기준서를 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return {"message": "입고검사 기준서를 삭제했습니다."}
