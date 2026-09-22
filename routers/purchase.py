from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.purchase import PurchaseMaster, PurchaseItem
from models.models import ItemMasterModel, WarehouseMasterModel, StorageLocationModel
from sqlalchemy import or_

router = APIRouter(prefix="/purchase", tags=["Purchase Management"])
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------------------------
# 1. Pydantic 스키마 정의
# ---------------------------------------------------------------------------
class PurchaseItemPayload(BaseModel):
    part_no: str
    part_name: str
    lot_no: Optional[str] = None
    qty: float
    unit: str = "EA"
    unit_price: float = 0.0
    supply_price: float = 0.0
    location_code: Optional[str] = None
    note: Optional[str] = None


class PurchaseCreatePayload(BaseModel):
    purchase_date: str
    partner_id: Optional[int] = None
    partner_name: str
    manager_name: Optional[str] = None
    remark: Optional[str] = None
    items: List[PurchaseItemPayload]


# ---------------------------------------------------------------------------
# 2. 화면 렌더링
# ---------------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
def purchase_page(current_user: dict = Depends(get_current_user)):
    """기존 /purchase 진입 경로는 표준 발주 입력 화면으로 연결."""
    return RedirectResponse(url="/purchase/orders", status_code=303)


# ---------------------------------------------------------------------------
# 3. 데이터 조회 API
# ---------------------------------------------------------------------------
@router.get("/api/list")
def get_purchase_list(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    partner_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    구매 전표 목록 그리드 조회
    """
    query = db.query(PurchaseMaster)

    if start_date:
        query = query.filter(PurchaseMaster.purchase_date >= start_date)
    if end_date:
        query = query.filter(PurchaseMaster.purchase_date <= end_date)
    if partner_name:
        query = query.filter(PurchaseMaster.partner_name.like(f"%{partner_name.strip()}%"))
    if status:
        query = query.filter(PurchaseMaster.status == status)

    records = query.order_by(PurchaseMaster.purchase_date.desc(), PurchaseMaster.id.desc()).all()

    results = []
    for r in records:
        total_qty = sum(item.qty for item in r.items)
        total_amount = sum(item.supply_price for item in r.items)
        item_summary = r.items[0].part_name if r.items else ""
        if len(r.items) > 1:
            item_summary += f" 외 {len(r.items) - 1}건"

        results.append({
            "id": r.id,
            "purchase_no": r.purchase_no,
            "purchase_date": r.purchase_date,
            "partner_name": r.partner_name,
            "item_summary": item_summary,
            "item_count": len(r.items),
            "total_qty": total_qty,
            "total_amount": total_amount,
            "status": r.status,
            "manager_name": r.manager_name or "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
        })
    return results


@router.get("/api/{purchase_id}")
def get_purchase_detail(
    purchase_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    구매 전표 단건 상세 조회 (모달 데이터 로드용)
    """
    master = db.query(PurchaseMaster).filter(PurchaseMaster.id == purchase_id).first()
    if not master:
        raise HTTPException(status_code=404, detail="구매 전표를 찾을 수 없습니다.")

    items = [
        {
            "id": item.id,
            "part_no": item.part_no,
            "part_name": item.part_name,
            "lot_no": item.lot_no or "",
            "qty": item.qty,
            "unit": item.unit,
            "unit_price": item.unit_price,
            "supply_price": item.supply_price,
            "location_code": item.location_code or "",
            "note": item.note or ""
        }
        for item in master.items
    ]

    return {
        "id": master.id,
        "purchase_no": master.purchase_no,
        "purchase_date": master.purchase_date,
        "partner_id": master.partner_id,
        "partner_name": master.partner_name,
        "status": master.status,
        "manager_name": master.manager_name or "",
        "remark": master.remark or "",
        "items": items
    }


# ---------------------------------------------------------------------------
# 4. 구매 전표 등록 및 삭제 API
# ---------------------------------------------------------------------------
@router.post("/api")
def create_purchase(
    payload: PurchaseCreatePayload,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    구매 전표 일괄 등록 (헤더 + 상세 품목)
    """
    if not payload.items:
        raise HTTPException(status_code=400, detail="등록할 품목이 최소 1개 이상 존재해야 합니다.")

    # 전표번호 채번: PU-YYYYMMDD-순번(3자리)
    date_str = payload.purchase_date.replace("-", "")
    prefix = f"PU-{date_str}-"

    last_record = (
        db.query(PurchaseMaster)
        .filter(PurchaseMaster.purchase_no.like(f"{prefix}%"))
        .order_by(PurchaseMaster.purchase_no.desc())
        .first()
    )

    if last_record:
        last_seq = int(last_record.purchase_no.split("-")[-1])
        new_seq = f"{last_seq + 1:03d}"
    else:
        new_seq = "001"

    purchase_no = f"{prefix}{new_seq}"

    new_master = PurchaseMaster(
        purchase_no=purchase_no,
        purchase_date=payload.purchase_date,
        partner_id=payload.partner_id,
        partner_name=payload.partner_name.strip(),
        manager_name=payload.manager_name.strip() if payload.manager_name else None,
        remark=payload.remark.strip() if payload.remark else None,
        status="COMPLETED"
    )

    for item in payload.items:
        if not item.part_no.strip():
            continue
        
        part_master = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == item.part_no.strip()).first()
        if not part_master:
            raise HTTPException(status_code=422, detail=f"등록되지 않은 품번입니다. ({item.part_no.strip()})")

        # 공급가액 계산 (수량 * 단가)
        calculated_supply = item.supply_price if item.supply_price > 0 else (item.qty * item.unit_price)

        new_master.items.append(
            PurchaseItem(
                item_id=part_master.id,
                part_no=part_master.part_no,
                part_name=item.part_name.strip(),
                lot_no=item.lot_no.strip() if item.lot_no else None,
                qty=item.qty,
                unit=item.unit.strip() if item.unit else "EA",
                unit_price=item.unit_price,
                supply_price=calculated_supply,
                location_code=item.location_code.strip() if item.location_code else None,
                note=item.note.strip() if item.note else None
            )
        )

    db.add(new_master)
    db.commit()
    db.refresh(new_master)
    return {"message": "구매(입고) 전표가 정상적으로 등록되었습니다.", "id": new_master.id, "purchase_no": purchase_no}


@router.delete("/api/{purchase_id}")
def delete_purchase(
    purchase_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """
    구매 전표 삭제
    """
    master = db.query(PurchaseMaster).filter(PurchaseMaster.id == purchase_id).first()
    if not master:
        raise HTTPException(status_code=404, detail="삭제할 전표를 찾을 수 없습니다.")

    db.delete(master)
    db.commit()
    return {"message": "구매 전표가 정상적으로 삭제되었습니다."}


# 신규 발주/입고 API. 기존 /purchase 화면과 API 경로는 유지합니다.
from datetime import date
from decimal import Decimal
from core.database import SessionLocal
from models.models import PurchaseOrderMaster, PurchaseOrderItem, PurchaseInboundMaster, PurchaseInboundItem
from schemas.purchase import OrderCreate, InboundCreate, OrderOut, InboundOut
from services.purchase_service import create_order, create_inbound, confirm_inbound, update_inbound_draft

api_router = APIRouter(prefix="/api/purchase", tags=["Purchase Orders / Inbound"])


def get_purchase_db():
    # Authentication uses get_db; keep writes in a separate fresh transaction.
    with SessionLocal() as db:
        yield db


@api_router.get("/vendors/search")
def search_purchase_vendors(
    keyword: str = Query("", max_length=100),
    offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    from models.partner import Partner
    query = db.query(Partner).filter(
        Partner.is_active == "Y", Partner.partner_type.in_(["VENDOR", "BOTH"])
    )
    keyword = keyword.strip()
    if keyword:
        query = query.filter(or_(
            Partner.partner_code.contains(keyword, autoescape=True),
            Partner.partner_name.contains(keyword, autoescape=True),
        ))
    total = query.count()
    rows = query.order_by(Partner.partner_code).offset(offset).limit(limit).all()
    return {"total": total, "items": [
        {"id": row.id, "partner_code": row.partner_code, "partner_name": row.partner_name,
         "manager_name": row.manager_name or ""} for row in rows
    ]}


def purchase_creator(user):
    value = user.get("username") if isinstance(user, dict) else getattr(user, "username", None)
    if not value or len(str(value)) > 50:
        raise HTTPException(422, "등록자 계정은 1~50자여야 합니다.")
    return str(value)


@api_router.post("/orders", response_model=OrderOut, status_code=201)
def post_order(payload: OrderCreate, db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user)):
    return create_order(db, payload, purchase_creator(current_user))


@api_router.get("/unreceived-orders")
@api_router.get("/orders/unreceived")
def unreceived_orders(
    partner_id: Optional[int] = Query(None, gt=0),
    partner_name: Optional[str] = Query(None, max_length=100),
    po_no: Optional[str] = Query(None, max_length=30),
    part_no: Optional[str] = Query(None, max_length=50),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    query = db.query(PurchaseOrderMaster, PurchaseOrderItem, ItemMasterModel).join(
    PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrderMaster.id
    ).join(ItemMasterModel, ItemMasterModel.id == PurchaseOrderItem.item_id).filter(
        PurchaseOrderMaster.status.in_(["ORDERED", "PARTIAL"]),
        PurchaseOrderItem.received_qty < PurchaseOrderItem.order_qty,
    )
    if partner_id is not None:
        query = query.filter(PurchaseOrderMaster.partner_id == partner_id)
    for value, column in [(partner_name, PurchaseOrderMaster.partner_name), (po_no, PurchaseOrderMaster.po_no), (part_no, PurchaseOrderItem.part_no)]:
        if value:
            query = query.filter(column.contains(value.strip(), autoescape=True))
    total = query.count()
    rows = query.order_by(PurchaseOrderMaster.order_date, PurchaseOrderMaster.id, PurchaseOrderItem.id).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "items": [
        {"po_id": m.id, "po_no": m.po_no, "order_date": m.order_date, "delivery_due_date": m.delivery_due_date,
         "partner_id": m.partner_id, "partner_name": m.partner_name, "manager_name": m.manager_name or "",
         "po_status": m.status,
         "po_item_id": i.id, "item_id": i.item_id, "part_no": p.part_no, "part_name": p.part_name, "spec": p.spec or "",
         "item_delivery_date": i.delivery_date,
         "order_qty": i.order_qty, "received_qty": i.received_qty,
         "remaining_qty": float(Decimal(str(i.order_qty)) - Decimal(str(i.received_qty))),
         "unit_price": i.unit_price, "unit": i.unit, "status": i.status} for m, i, p in rows]} 


@api_router.post("/inbound", response_model=InboundOut, status_code=201)
def post_inbound(payload: InboundCreate, db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user)):
    return create_inbound(db, payload, purchase_creator(current_user))


@api_router.post("/inbound/drafts", response_model=InboundOut, status_code=201)
def save_inbound_draft(payload: InboundCreate, db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user)):
    return create_inbound(db, payload, purchase_creator(current_user), draft=True)


@api_router.get("/inbound/drafts", response_model=list[InboundOut])
def list_inbound_drafts(
    inbound_no: Optional[str] = Query(None, max_length=30),
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    query = db.query(PurchaseInboundMaster).filter(PurchaseInboundMaster.status == "DRAFT")
    if inbound_no:
        query = query.filter(PurchaseInboundMaster.inbound_no.contains(inbound_no.strip(), autoescape=True))
    return query.order_by(PurchaseInboundMaster.id.desc()).limit(30).all()


@api_router.get("/inbound/drafts/{inbound_id}")
def get_inbound_draft(
    inbound_id: int, db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    master = db.get(PurchaseInboundMaster, inbound_id)
    if master is None or master.status != "DRAFT":
        raise HTTPException(404, "임시저장된 구매 입력을 찾을 수 없습니다.")
    items = []
    for row in master.items:
        po_item = db.get(PurchaseOrderItem, row.po_item_id) if row.po_item_id else None
        part = db.get(ItemMasterModel, row.item_id) if row.item_id else db.query(ItemMasterModel).filter(ItemMasterModel.part_no == row.part_no).one_or_none()
        items.append({
            "po_item_id": row.po_item_id, "po_no": po_item.order.po_no if po_item else "",
            "item_id": row.item_id, "part_no": part.part_no if part else row.part_no, "part_name": part.part_name if part else "",
            "spec": (part.spec or "") if part else "", "unit": row.unit,
            "order_qty": po_item.order_qty if po_item else None,
            "remaining_qty": po_item.order_qty - po_item.received_qty if po_item else None,
            "delivery_date": po_item.delivery_date if po_item else None,
            "inbound_qty": row.inbound_qty, "supplier_lot_no": row.supplier_lot_no,
            "warehouse_code": row.warehouse_code, "storage_location": row.storage_location,
            "note": row.note or "",
        })
    first_po_item = db.get(PurchaseOrderItem, master.items[0].po_item_id) if master.items and master.items[0].po_item_id else None
    return {"id": master.id, "inbound_no": master.inbound_no, "inbound_date": master.inbound_date,
            "partner_id": master.partner_id, "partner_name": master.partner_name,
            "manager_name": first_po_item.order.manager_name if first_po_item else "",
            "invoice_no": master.invoice_no or "", "note": master.note or "", "items": items}


@api_router.put("/inbound/drafts/{inbound_id}", response_model=InboundOut)
def revise_inbound_draft(
    inbound_id: int, payload: InboundCreate,
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    return update_inbound_draft(db, inbound_id, payload)


@api_router.post("/inbound/drafts/{inbound_id}/confirm", response_model=InboundOut)
def confirm_inbound_draft(
    inbound_id: int, db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    return confirm_inbound(db, inbound_id)


@api_router.get("/inbound/history")
def inbound_history(
    start_date: Optional[date] = None, end_date: Optional[date] = None,
    partner_id: Optional[int] = Query(None, gt=0),
    partner_name: Optional[str] = Query(None, max_length=100),
    supplier_lot_no: Optional[str] = Query(None, max_length=100),
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "시작일은 종료일 이후일 수 없습니다.")
    query = db.query(PurchaseInboundMaster, PurchaseInboundItem, PurchaseOrderMaster.po_no).join(
        PurchaseInboundItem, PurchaseInboundItem.inbound_id == PurchaseInboundMaster.id
    ).outerjoin(PurchaseOrderItem, PurchaseOrderItem.id == PurchaseInboundItem.po_item_id).outerjoin(
        PurchaseOrderMaster, PurchaseOrderMaster.id == PurchaseOrderItem.po_id
    ).filter(PurchaseInboundMaster.status == "CONFIRMED")
    if start_date:
        query = query.filter(PurchaseInboundMaster.inbound_date >= start_date.isoformat())
    if end_date:
        query = query.filter(PurchaseInboundMaster.inbound_date <= end_date.isoformat())
    if partner_id is not None:
        query = query.filter(PurchaseInboundMaster.partner_id == partner_id)
    if partner_name:
        query = query.filter(PurchaseInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if supplier_lot_no:
        query = query.filter(PurchaseInboundItem.supplier_lot_no == supplier_lot_no.strip())
    total = query.count()
    rows = query.order_by(PurchaseInboundMaster.inbound_date.desc(), PurchaseInboundMaster.id.desc(), PurchaseInboundItem.id).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "items": [
        {"inbound_id": m.id, "inbound_no": m.inbound_no, "inbound_date": m.inbound_date,
         "partner_id": m.partner_id, "partner_name": m.partner_name, "invoice_no": m.invoice_no,
         "created_by": m.created_by, "created_at": m.created_at,
         "inbound_item_id": i.id, "po_item_id": i.po_item_id, "po_no": po_no,
         "item_id": i.item_id, "part_no": i.part_no, "inbound_qty": i.inbound_qty, "unit_price": i.unit_price,
         "unit": i.unit, "inspection_status": i.inspection_status,
         "supplier_lot_no": i.supplier_lot_no, "internal_lot_no": i.internal_lot_no,
         "warehouse_code": i.warehouse_code, "storage_location": i.storage_location} for m, i, po_no in rows]}


@api_router.get("/items/search")
def search_purchase_items(
    keyword: str = Query("", max_length=100),
    offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")
    keyword = keyword.strip()
    if keyword:
        query = query.filter(or_(
            ItemMasterModel.part_no.contains(keyword, autoescape=True),
            ItemMasterModel.part_name.contains(keyword, autoescape=True),
            ItemMasterModel.spec.contains(keyword, autoescape=True),
        ))
    total = query.count()
    rows = query.order_by(ItemMasterModel.part_no).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit, "items": [
        {"item_id": item.id, "part_no": item.part_no, "part_name": item.part_name, "spec": item.spec or "", "unit": item.unit}
        for item in rows
    ]}


@api_router.get("/orders")
def purchase_order_list(
    status: Optional[str] = Query(None, max_length=20),
    supplier: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=50),
    offset: int = Query(0, ge=0), limit: int = Query(1000, ge=1, le=1000),
    db: Session = Depends(get_purchase_db), current_user=Depends(get_current_user),
):
    """Supply the existing dashboard's response shape from the PO tables."""
    from sqlalchemy import func, distinct
    labels = {"ORDERED": "발주완료", "PARTIAL": "부분입고", "COMPLETED": "입고완료", "CANCELLED": "취소"}
    query = db.query(PurchaseOrderMaster, PurchaseOrderItem, ItemMasterModel).join(
        PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrderMaster.id
    ).join(ItemMasterModel, ItemMasterModel.id == PurchaseOrderItem.item_id)
    if status and status != "ALL":
        selected = {**{value: key for key, value in labels.items()}, "입고대기": "PARTIAL"}.get(status, status)
        if selected not in labels:
            raise HTTPException(422, "지원하지 않는 발주 상태입니다.")
        query = query.filter(PurchaseOrderMaster.status == selected)
    if supplier:
        query = query.filter(PurchaseOrderMaster.partner_name.contains(supplier.strip(), autoescape=True))
    if part_no:
        query = query.filter(PurchaseOrderItem.part_no.contains(part_no.strip(), autoescape=True))
    counts = dict(query.with_entities(PurchaseOrderMaster.status, func.count(distinct(PurchaseOrderMaster.id))).group_by(PurchaseOrderMaster.status).all())
    total = query.count()
    rows = query.order_by(PurchaseOrderMaster.id.desc(), PurchaseOrderItem.id).offset(offset).limit(limit).all()
    return {"total": total, "offset": offset, "limit": limit,
            "stats": {"total": sum(counts.values()), "waiting": counts.get("ORDERED", 0) + counts.get("PARTIAL", 0), "completed": counts.get("COMPLETED", 0)},
            "data": [{"id": m.id, "po_no": m.po_no, "order_date": m.order_date,
                      "supplier_name": m.partner_name, "part_no": i.part_no, "part_name": p.part_name,
                      "order_qty": i.order_qty, "unit": i.unit, "due_date": m.delivery_due_date or "",
                      "delivery_date": i.delivery_date or "", "item_note": i.note or "",
                      "manager_name": m.manager_name or "", "note": m.note or "",
                      "status": labels[m.status]} for m, i, p in rows]}

