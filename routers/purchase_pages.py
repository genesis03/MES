from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import StorageLocationModel, WarehouseMasterModel

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
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "발주 조회",
            "page_description": "일반구매와 외주가공 발주를 통합 조회하는 화면입니다.",
            "route_path": "/purchase/inquiry/orders",
        },
    )


@router.get("/purchase/inquiry/inbounds", response_class=HTMLResponse)
def purchase_inbound_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="purchase_route_ready.html",
        context={
            "request": request,
            "user": current_user,
            "page_title": "입고 조회",
            "page_description": "일반구매와 외주가공 입고 이력을 통합 조회하는 화면입니다.",
            "route_path": "/purchase/inquiry/inbounds",
        },
    )


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
