from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.partner import Partner, PartnerContact

router = APIRouter(prefix="/basic-info/partners", tags=["BasicInfo - Partners"])
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# 1. 내부 Pydantic 스키마 정의
# ---------------------------------------------------------------------------
class ContactItem(BaseModel):
    id: Optional[int] = None
    contact_name: str
    department: Optional[str] = None
    position: Optional[str] = None
    handled_item: Optional[str] = None
    extension_tel: Optional[str] = None
    mobile_phone: Optional[str] = None
    email: Optional[str] = None


class PartnerPayload(BaseModel):
    partner_code: str
    partner_name: str
    partner_type: str = "VENDOR"
    ceo_name: Optional[str] = None
    tel: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    tax_email: Optional[str] = None
    post_code: Optional[str] = None
    address_base: Optional[str] = None
    address_detail: Optional[str] = None
    manager_name: Optional[str] = None
    is_active: str = "Y"
    remark: Optional[str] = None
    contacts: List[ContactItem] = []


# ---------------------------------------------------------------------------
# 2. 화면 렌더링 (TemplateResponse 호출 규격 최적화)
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def partner_page(request: Request, current_user: dict = Depends(get_current_user)):
    """
    거래처 관리 메인 화면 반환
    """
    return templates.TemplateResponse(
        request=request,
        name="partners.html",
        context={"user": current_user}
    )


# ---------------------------------------------------------------------------
# 3. 데이터 조회 API
# ---------------------------------------------------------------------------
@router.get("/api/list")
def get_partner_list(
    partner_code: Optional[str] = Query(None),
    partner_name: Optional[str] = Query(None),
    partner_type: Optional[str] = Query(None),
    is_active: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    메인 리스트 그리드 데이터 조회
    """
    query = db.query(Partner)
    
    if partner_code:
        query = query.filter(Partner.partner_code.like(f"%{partner_code.strip()}%"))
    if partner_name:
        query = query.filter(Partner.partner_name.like(f"%{partner_name.strip()}%"))
    if partner_type:
        query = query.filter(Partner.partner_type == partner_type)
    if is_active:
        query = query.filter(Partner.is_active == is_active)

    partners = query.order_by(Partner.id.desc()).all()
    
    results = []
    for p in partners:
        results.append({
            "id": p.id,
            "partner_code": p.partner_code,
            "partner_name": p.partner_name,
            "partner_type": p.partner_type,
            "ceo_name": p.ceo_name or "",
            "tel": p.tel or "",
            "mobile": p.mobile or "",
            "fax": p.fax or "",
            "tax_email": p.tax_email or "",
            "post_code": p.post_code or "",
            "address_base": p.address_base or "",
            "address_detail": p.address_detail or "",
            "manager_name": p.manager_name or "",
            "is_active": p.is_active,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else ""
        })
    return results


@router.get("/api/lookup")
def get_partner_lookup(
    partner_type: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    공통 참조용 경량 조회 API
    """
    query = db.query(Partner).filter(Partner.is_active == "Y")
    
    if partner_type:
        query = query.filter(Partner.partner_type.in_([partner_type, "BOTH"]))
        
    partners = query.order_by(Partner.partner_name.asc()).all()
    return [
        {
            "code": p.partner_code,
            "name": p.partner_name,
            "type": p.partner_type
        }
        for p in partners
    ]


@router.get("/api/{partner_id}")
def get_partner_detail(
    partner_id: int, 
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    단건 상세 조회 (모달 데이터 로드용)
    """
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="거래처 정보를 찾을 수 없습니다.")

    contacts = [
        {
            "id": c.id,
            "contact_name": c.contact_name,
            "department": c.department or "",
            "position": c.position or "",
            "handled_item": c.handled_item or "",
            "extension_tel": c.extension_tel or "",
            "mobile_phone": c.mobile_phone or "",
            "email": c.email or ""
        }
        for c in partner.contacts
    ]

    return {
        "id": partner.id,
        "partner_code": partner.partner_code,
        "partner_name": partner.partner_name,
        "partner_type": partner.partner_type,
        "ceo_name": partner.ceo_name or "",
        "tel": partner.tel or "",
        "mobile": partner.mobile or "",
        "fax": partner.fax or "",
        "tax_email": partner.tax_email or "",
        "post_code": partner.post_code or "",
        "address_base": partner.address_base or "",
        "address_detail": partner.address_detail or "",
        "manager_name": partner.manager_name or "",
        "is_active": partner.is_active,
        "remark": partner.remark or "",
        "contacts": contacts
    }


# ---------------------------------------------------------------------------
# 4. 데이터 등록 및 수정 API
# ---------------------------------------------------------------------------
@router.post("/api")
def create_partner(
    payload: PartnerPayload, 
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    거래처 신규 등록
    """
    exist = db.query(Partner).filter(Partner.partner_code == payload.partner_code.strip()).first()
    if exist:
        raise HTTPException(status_code=400, detail="이미 등록된 거래처코드입니다.")

    new_partner = Partner(
        partner_code=payload.partner_code.strip(),
        partner_name=payload.partner_name.strip(),
        partner_type=payload.partner_type,
        ceo_name=payload.ceo_name.strip() if payload.ceo_name else None,
        tel=payload.tel.strip() if payload.tel else None,
        mobile=payload.mobile.strip() if payload.mobile else None,
        fax=payload.fax.strip() if payload.fax else None,
        tax_email=payload.tax_email.strip() if payload.tax_email else None,
        post_code=payload.post_code.strip() if payload.post_code else None,
        address_base=payload.address_base.strip() if payload.address_base else None,
        address_detail=payload.address_detail.strip() if payload.address_detail else None,
        manager_name=payload.manager_name.strip() if payload.manager_name else None,
        is_active=payload.is_active,
        remark=payload.remark.strip() if payload.remark else None
    )

    for c in payload.contacts:
        if c.contact_name and c.contact_name.strip():
            new_partner.contacts.append(
                PartnerContact(
                    contact_name=c.contact_name.strip(),
                    department=c.department.strip() if c.department else None,
                    position=c.position.strip() if c.position else None,
                    handled_item=c.handled_item.strip() if c.handled_item else None,
                    extension_tel=c.extension_tel.strip() if c.extension_tel else None,
                    mobile_phone=c.mobile_phone.strip() if c.mobile_phone else None,
                    email=c.email.strip() if c.email else None
                )
            )

    db.add(new_partner)
    db.commit()
    db.refresh(new_partner)
    return {"message": "정상적으로 등록되었습니다.", "id": new_partner.id}


@router.put("/api/{partner_id}")
def update_partner(
    partner_id: int, 
    payload: PartnerPayload, 
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    거래처 정보 수정
    """
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="수정할 거래처를 찾을 수 없습니다.")

    dup = db.query(Partner).filter(
        Partner.partner_code == payload.partner_code.strip(),
        Partner.id != partner_id
    ).first()
    if dup:
        raise HTTPException(status_code=400, detail="이미 사용 중인 거래처코드입니다.")

    partner.partner_code = payload.partner_code.strip()
    partner.partner_name = payload.partner_name.strip()
    partner.partner_type = payload.partner_type
    partner.ceo_name = payload.ceo_name.strip() if payload.ceo_name else None
    partner.tel = payload.tel.strip() if payload.tel else None
    partner.mobile = payload.mobile.strip() if payload.mobile else None
    partner.fax = payload.fax.strip() if payload.fax else None
    partner.tax_email = payload.tax_email.strip() if payload.tax_email else None
    partner.post_code = payload.post_code.strip() if payload.post_code else None
    partner.address_base = payload.address_base.strip() if payload.address_base else None
    partner.address_detail = payload.address_detail.strip() if payload.address_detail else None
    partner.manager_name = payload.manager_name.strip() if payload.manager_name else None
    partner.is_active = payload.is_active
    partner.remark = payload.remark.strip() if payload.remark else None
    partner.updated_at = datetime.now()

    partner.contacts.clear()
    for c in payload.contacts:
        if c.contact_name and c.contact_name.strip():
            partner.contacts.append(
                PartnerContact(
                    contact_name=c.contact_name.strip(),
                    department=c.department.strip() if c.department else None,
                    position=c.position.strip() if c.position else None,
                    handled_item=c.handled_item.strip() if c.handled_item else None,
                    extension_tel=c.extension_tel.strip() if c.extension_tel else None,
                    mobile_phone=c.mobile_phone.strip() if c.mobile_phone else None,
                    email=c.email.strip() if c.email else None
                )
            )

    db.commit()
    return {"message": "성공적으로 수정되었습니다.", "id": partner.id}

@router.delete("/api/{partner_id}")
def delete_partner(
    partner_id: int, 
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    거래처 삭제 API (종속된 담당자 목록도 cascade 설정에 의해 자동 삭제)
    """
    partner = db.query(Partner).filter(Partner.id == partner_id).first()
    if not partner:
        raise HTTPException(status_code=404, detail="삭제할 거래처 정보를 찾을 수 없습니다.")

    db.delete(partner)
    db.commit()
    return {"message": "정상적으로 삭제되었습니다."}