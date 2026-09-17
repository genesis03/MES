from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import (
    ItemMasterModel,
    PurchaseInboundItem,
    PurchaseInboundMaster,
    PurchaseOrderItem,
    PurchaseOrderMaster,
    StorageLocationModel,
    WarehouseMasterModel,
)
from models.partner import Partner

router = APIRouter(tags=["Purchase Pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/purchase/orders", response_class=HTMLResponse)
def purchase_orders_page(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """일반구매 발주 입력 표준 화면."""
    warehouses = db.query(WarehouseMasterModel).order_by(WarehouseMasterModel.warehouse_code).all()
    storage_locations = db.query(StorageLocationModel).order_by(StorageLocationModel.location_code).all()
    vendors = (
        db.query(Partner)
        .filter(
            Partner.is_active == "Y",
            Partner.partner_type.in_(["VENDOR", "BOTH"]),
        )
        .order_by(Partner.partner_name.asc())
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="purchase.html",
        context={
            "request": request,
            "user": current_user,
            "today": datetime.now().strftime("%Y-%m-%d"),
            "warehouses": warehouses,
            "warehouse_masters": warehouses,
            "storage_locations": storage_locations,
            "vendors": vendors,
        },
    )


@router.get("/purchase/inbound", response_class=HTMLResponse)
def purchase_inbound_page(
    request: Request,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """일반구매 입고 전용 화면."""
    warehouses = (
        db.query(WarehouseMasterModel)
        .filter(WarehouseMasterModel.is_active == "Y")
        .order_by(WarehouseMasterModel.warehouse_code)
        .all()
    )
    storage_locations = (
        db.query(StorageLocationModel)
        .filter(StorageLocationModel.is_active == "Y")
        .order_by(StorageLocationModel.location_code)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="purchase_inbound.html",
        context={
            "request": request,
            "user": current_user,
            "warehouse_masters": warehouses,
            "storage_locations": storage_locations,
        },
    )


@router.get("/purchase/inquiry/orders", response_class=HTMLResponse)
def purchase_order_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_order_inquiry.html",
        context={"request": request, "user": current_user},
    )


@router.get("/purchase/inquiry/inbounds", response_class=HTMLResponse)
def purchase_inbound_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_inbound_inquiry.html",
        context={"request": request, "user": current_user},
    )


@router.get("/api/purchase/inbound/order-picker")
def purchase_inbound_order_picker(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    po_no: Optional[str] = Query(None, max_length=30),
    part_no: Optional[str] = Query(None, max_length=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="시작일은 종료일 이후일 수 없습니다.")

    query = (
        db.query(PurchaseOrderMaster, PurchaseOrderItem, ItemMasterModel)
        .join(PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrderMaster.id)
        .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseOrderItem.part_no)
        .filter(
            PurchaseOrderMaster.status.in_(["ORDERED", "PARTIAL"]),
            PurchaseOrderItem.received_qty < PurchaseOrderItem.order_qty,
        )
    )
    if start_date:
        query = query.filter(PurchaseOrderMaster.order_date >= start_date)
    if end_date:
        query = query.filter(PurchaseOrderMaster.order_date <= end_date)
    if po_no and po_no.strip():
        query = query.filter(PurchaseOrderMaster.po_no.contains(po_no.strip(), autoescape=True))
    if part_no and part_no.strip():
        query = query.filter(PurchaseOrderItem.part_no.contains(part_no.strip(), autoescape=True))

    rows = query.order_by(
        PurchaseOrderMaster.order_date.desc(),
        PurchaseOrderMaster.id.desc(),
        PurchaseOrderItem.id.asc(),
    ).limit(3000).all()

    status_names = {"ORDERED": "발주완료", "PARTIAL": "부분입고"}
    grouped = {}
    for master, item, part in rows:
        order = grouped.setdefault(master.id, {
            "po_id": master.id,
            "po_no": master.po_no,
            "partner_id": master.partner_id,
            "partner_name": master.partner_name,
            "order_date": master.order_date,
            "status": master.status,
            "status_name": status_names.get(master.status, master.status),
            "related_sales_order_no": "",
            "updated_at": (master.updated_at or master.created_at).strftime("%Y-%m-%d %H:%M:%S") if (master.updated_at or master.created_at) else "",
            "manager_name": master.manager_name or "",
            "items": [],
        })
        order["items"].append({
            "po_item_id": item.id,
            "po_no": master.po_no,
            "partner_id": master.partner_id,
            "partner_name": master.partner_name,
            "manager_name": master.manager_name or "",
            "part_no": item.part_no,
            "part_name": part.part_name,
            "spec": part.spec or "",
            "unit": item.unit,
            "order_qty": item.order_qty,
            "received_qty": item.received_qty,
            "remaining_qty": max(float(item.order_qty or 0) - float(item.received_qty or 0), 0.0),
            "item_delivery_date": item.delivery_date or "",
            "storage_location": item.storage_location or "",
            "status": item.status,
        })
    return {"total": len(grouped), "orders": list(grouped.values())}


@router.get("/api/purchase/inquiry/orders")
def purchase_order_inquiry_api(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    po_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=50),
    status: Optional[str] = Query(None, max_length=20),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    labels = {
        "ORDERED": "발주완료",
        "PARTIAL": "부분입고",
        "COMPLETED": "입고완료",
        "CANCELLED": "취소",
    }
    if status and status not in labels:
        raise HTTPException(status_code=422, detail="지원하지 않는 발주 상태입니다.")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="시작일은 종료일 이후일 수 없습니다.")

    query = (
        db.query(PurchaseOrderMaster, PurchaseOrderItem, ItemMasterModel)
        .join(PurchaseOrderItem, PurchaseOrderItem.po_id == PurchaseOrderMaster.id)
        .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseOrderItem.part_no)
    )
    if start_date:
        query = query.filter(PurchaseOrderMaster.order_date >= start_date)
    if end_date:
        query = query.filter(PurchaseOrderMaster.order_date <= end_date)
    if po_no:
        query = query.filter(PurchaseOrderMaster.po_no.contains(po_no.strip(), autoescape=True))
    if partner_name:
        query = query.filter(PurchaseOrderMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if part_no:
        query = query.filter(PurchaseOrderItem.part_no.contains(part_no.strip(), autoescape=True))
    if status:
        query = query.filter(PurchaseOrderMaster.status == status)

    total = query.count()
    rows = query.order_by(PurchaseOrderMaster.order_date.desc(), PurchaseOrderMaster.id.desc(), PurchaseOrderItem.id).limit(2000).all()
    items = []
    for master, item, part in rows:
        items.append({
            "type": "GENERAL",
            "po_id": master.id,
            "po_no": master.po_no,
            "order_date": master.order_date,
            "delivery_due_date": master.delivery_due_date or "",
            "partner_id": master.partner_id,
            "partner_name": master.partner_name,
            "manager_name": master.manager_name or "",
            "part_no": item.part_no,
            "part_name": part.part_name,
            "spec": part.spec or "",
            "order_qty": item.order_qty,
            "unit": item.unit,
            "item_delivery_date": item.delivery_date or "",
            "status": master.status,
            "status_name": labels.get(master.status, master.status),
            "note": item.note or master.note or "",
        })
    return {"total": total, "items": items}


@router.get("/api/purchase/inquiry/inbounds")
def purchase_inbound_inquiry_api(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    inbound_no: Optional[str] = Query(None, max_length=30),
    po_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=50),
    lot: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="시작일은 종료일 이후일 수 없습니다.")

    query = (
        db.query(PurchaseInboundMaster, PurchaseInboundItem, ItemMasterModel, PurchaseOrderMaster.po_no)
        .join(PurchaseInboundItem, PurchaseInboundItem.inbound_id == PurchaseInboundMaster.id)
        .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseInboundItem.part_no)
        .outerjoin(PurchaseOrderItem, PurchaseOrderItem.id == PurchaseInboundItem.po_item_id)
        .outerjoin(PurchaseOrderMaster, PurchaseOrderMaster.id == PurchaseOrderItem.po_id)
        .filter(PurchaseInboundMaster.status == "CONFIRMED")
    )
    if start_date:
        query = query.filter(PurchaseInboundMaster.inbound_date >= start_date)
    if end_date:
        query = query.filter(PurchaseInboundMaster.inbound_date <= end_date)
    if inbound_no:
        query = query.filter(PurchaseInboundMaster.inbound_no.contains(inbound_no.strip(), autoescape=True))
    if po_no:
        query = query.filter(PurchaseOrderMaster.po_no.contains(po_no.strip(), autoescape=True))
    if partner_name:
        query = query.filter(PurchaseInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if part_no:
        query = query.filter(PurchaseInboundItem.part_no.contains(part_no.strip(), autoescape=True))
    if lot:
        keyword = lot.strip()
        query = query.filter(or_(
            PurchaseInboundItem.supplier_lot_no.contains(keyword, autoescape=True),
            PurchaseInboundItem.internal_lot_no.contains(keyword, autoescape=True),
        ))

    total = query.count()
    rows = query.order_by(PurchaseInboundMaster.inbound_date.desc(), PurchaseInboundMaster.id.desc(), PurchaseInboundItem.id).limit(2000).all()
    items = []
    for master, item, part, order_no in rows:
        items.append({
            "type": "GENERAL",
            "inbound_id": master.id,
            "inbound_no": master.inbound_no,
            "inbound_date": master.inbound_date,
            "po_no": order_no or "",
            "partner_id": master.partner_id,
            "partner_name": master.partner_name,
            "part_no": item.part_no,
            "part_name": part.part_name,
            "spec": part.spec or "",
            "inbound_qty": item.inbound_qty,
            "unit": item.unit,
            "supplier_lot_no": item.supplier_lot_no,
            "internal_lot_no": item.internal_lot_no or "",
            "warehouse_code": item.warehouse_code,
            "storage_location": item.storage_location,
            "inspection_status": item.inspection_status,
            "note": item.note or master.note or "",
        })
    return {"total": total, "items": items}


@router.get("/purchase/unreceived", response_class=HTMLResponse)
def purchase_unreceived_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "미구매 현황",
            "page_description": "일반구매와 외주가공의 미입고 잔량을 통합 관리하는 관제 화면입니다.",
            "route_path": "/purchase/unreceived",
        },
    )


@router.get("/subcontract/orders", response_class=HTMLResponse)
def subcontract_orders_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "발주 입력 (외주가공)",
            "page_description": "외주가공 발주 기능 구현을 위한 표준 진입 경로입니다.",
            "route_path": "/subcontract/orders",
        },
    )


@router.get("/subcontract/outbound", response_class=HTMLResponse)
def subcontract_outbound_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "외주 가공 출고 처리",
            "page_description": "사급 반출 및 외주 이동재고 처리 기능을 위한 표준 진입 경로입니다.",
            "route_path": "/subcontract/outbound",
        },
    )


@router.get("/subcontract/inbound", response_class=HTMLResponse)
def subcontract_inbound_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "외주 가공 입고 처리",
            "page_description": "외주 완료품 입고 및 LOT 연계 기능을 위한 표준 진입 경로입니다.",
            "route_path": "/subcontract/inbound",
        },
    )
