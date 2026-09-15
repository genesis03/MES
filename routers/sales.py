from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel
from models.packing import PackingBox, PackingMaster
from models.partner import Partner
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentBox, ShipmentItem, ShipmentMaster

router = APIRouter(tags=["Sales"])
templates = Jinja2Templates(directory="templates")


class SalesOrderItemInput(BaseModel):
    part_no: str = Field(min_length=1, max_length=50)
    order_qty: float = Field(gt=0)
    delivery_date: Optional[str] = Field(default=None, max_length=10)
    note: Optional[str] = Field(default=None, max_length=500)


class SalesOrderCreateInput(BaseModel):
    order_date: str = Field(min_length=10, max_length=10)
    delivery_due_date: Optional[str] = Field(default=None, max_length=10)
    customer_id: int = Field(gt=0)
    manager_name: Optional[str] = Field(default=None, max_length=50)
    note: Optional[str] = Field(default=None, max_length=1000)
    items: list[SalesOrderItemInput] = Field(min_length=1)


class ShipmentCreateInput(BaseModel):
    shipment_date: str = Field(min_length=10, max_length=10)
    sales_order_item_id: int = Field(gt=0)
    packing_box_ids: list[int] = Field(min_length=1)
    note: Optional[str] = Field(default=None, max_length=1000)


def _username(user) -> str:
    return str(getattr(user, "username", None) or getattr(user, "name", None) or "")


def _next_no(db: Session, model, field, prefix: str, date_value: str) -> str:
    yymmdd = date_value.replace("-", "")[2:]
    base = f"{prefix}{yymmdd}"
    latest = db.query(field).filter(field.like(base + "%")).order_by(field.desc()).first()
    seq = 1
    if latest:
        try:
            seq = int(str(latest[0])[-3:]) + 1
        except (TypeError, ValueError):
            seq = 1
    return f"{base}{seq:03d}"


def _sync_order_status(order: SalesOrderMaster):
    if not order.items:
        order.status = "ORDERED"
        return
    if all(float(x.shipped_qty or 0) >= float(x.order_qty or 0) - 1e-9 for x in order.items):
        order.status = "COMPLETED"
    elif any(float(x.shipped_qty or 0) > 1e-9 for x in order.items):
        order.status = "PARTIAL"
    else:
        order.status = "ORDERED"

    for item in order.items:
        shipped = float(item.shipped_qty or 0)
        ordered = float(item.order_qty or 0)
        if shipped >= ordered - 1e-9:
            item.status = "COMPLETED"
        elif shipped > 1e-9:
            item.status = "PARTIAL"
        else:
            item.status = "WAITING"


@router.get("/sales/orders", response_class=HTMLResponse)
def sales_order_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="sales_orders.html", context={"request": request, "user": current_user})


@router.get("/sales/orders/inquiry", response_class=HTMLResponse)
def sales_order_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="sales_orders_inquiry.html", context={"request": request, "user": current_user})


@router.get("/sales/unsold", response_class=HTMLResponse)
def sales_unsold_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="sales_unsold.html", context={"request": request, "user": current_user})


@router.get("/sales/shipping", response_class=HTMLResponse)
def shipping_entry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="sales_shipping.html", context={"request": request, "user": current_user})


@router.get("/api/sales/customers")
def sales_customers(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = (
        db.query(Partner)
        .filter(Partner.is_active == "Y", Partner.partner_type.in_(["CUSTOMER", "BOTH"]))
        .order_by(Partner.partner_name.asc())
        .all()
    )
    return [{"id": x.id, "partner_code": x.partner_code, "partner_name": x.partner_name} for x in rows]


@router.get("/api/sales/items")
def sales_items(
    q: Optional[str] = Query(None, max_length=80),
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = (q or "").strip()
    query = db.query(ItemMasterModel).filter(
        ItemMasterModel.is_active == "Y",
        ItemMasterModel.material_type.in_(["SEMI", "FINISHED"]),
    )
    if keyword:
        query = query.filter(ItemMasterModel.part_no.contains(keyword, autoescape=True))
    rows = query.order_by(ItemMasterModel.part_no.asc()).limit(limit).all()
    return [
        {
            "part_no": x.part_no,
            "part_name": x.part_name,
            "unit": x.unit or "EA",
            "material_type": x.material_type,
        }
        for x in rows
    ]


@router.post("/api/sales/orders")
def create_sales_order(payload: SalesOrderCreateInput, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    customer = db.query(Partner).filter(
        Partner.id == payload.customer_id,
        Partner.is_active == "Y",
        Partner.partner_type.in_(["CUSTOMER", "BOTH"]),
    ).first()
    if not customer:
        raise HTTPException(404, "사용 가능한 판매처를 찾을 수 없습니다.")

    part_nos = [x.part_no.strip() for x in payload.items]
    if len(set(part_nos)) != len(part_nos):
        raise HTTPException(409, "동일 품번은 수주 한 건에 중복 입력할 수 없습니다.")

    item_rows = db.query(ItemMasterModel).filter(
        ItemMasterModel.part_no.in_(part_nos),
        ItemMasterModel.is_active == "Y",
        ItemMasterModel.material_type.in_(["SEMI", "FINISHED"]),
    ).all()
    item_map = {x.part_no: x for x in item_rows}
    missing = [x for x in part_nos if x not in item_map]
    if missing:
        raise HTTPException(404, f"수주 가능한 완제품/반제품을 찾을 수 없습니다: {', '.join(missing)}")

    order = SalesOrderMaster(
        order_no=_next_no(db, SalesOrderMaster, SalesOrderMaster.order_no, "SO", payload.order_date),
        order_date=payload.order_date,
        delivery_due_date=payload.delivery_due_date or None,
        customer_id=customer.id,
        customer_name=customer.partner_name,
        status="ORDERED",
        manager_name=(payload.manager_name or "").strip() or None,
        note=(payload.note or "").strip() or None,
        created_by=_username(current_user),
    )
    db.add(order)
    db.flush()

    for row in payload.items:
        master = item_map[row.part_no.strip()]
        order.items.append(SalesOrderItem(
            part_no=master.part_no,
            part_name=master.part_name,
            order_qty=float(row.order_qty),
            shipped_qty=0.0,
            unit=master.unit or "EA",
            delivery_date=row.delivery_date or payload.delivery_due_date or None,
            note=(row.note or "").strip() or None,
            status="WAITING",
        ))

    db.commit()
    db.refresh(order)
    return {"id": order.id, "order_no": order.order_no, "message": "수주가 등록되었습니다."}


@router.get("/api/sales/orders")
def sales_orders(
    status: Optional[str] = Query(None, max_length=20),
    customer_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(SalesOrderMaster)
    if status:
        query = query.filter(SalesOrderMaster.status == status)
    if customer_id:
        query = query.filter(SalesOrderMaster.customer_id == customer_id)
    rows = query.order_by(SalesOrderMaster.order_date.desc(), SalesOrderMaster.id.desc()).limit(500).all()
    return [{
        "id": x.id,
        "order_no": x.order_no,
        "order_date": x.order_date,
        "delivery_due_date": x.delivery_due_date,
        "customer_id": x.customer_id,
        "customer_name": x.customer_name,
        "status": x.status,
        "manager_name": x.manager_name,
        "note": x.note,
        "items": [{
            "id": i.id,
            "part_no": i.part_no,
            "part_name": i.part_name,
            "order_qty": i.order_qty,
            "shipped_qty": i.shipped_qty,
            "remaining_qty": max(float(i.order_qty or 0) - float(i.shipped_qty or 0), 0.0),
            "unit": i.unit,
            "delivery_date": i.delivery_date,
            "status": i.status,
        } for i in x.items],
    } for x in rows]


@router.get("/api/sales/unsold")
def sales_unsold(
    customer_id: Optional[int] = None,
    part_no: Optional[str] = Query(None, max_length=50),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = (
        db.query(SalesOrderItem, SalesOrderMaster)
        .join(SalesOrderMaster, SalesOrderMaster.id == SalesOrderItem.order_id)
        .filter(
            SalesOrderMaster.status.in_(["ORDERED", "PARTIAL"]),
            SalesOrderItem.shipped_qty < SalesOrderItem.order_qty,
        )
    )
    if customer_id:
        query = query.filter(SalesOrderMaster.customer_id == customer_id)
    if part_no and part_no.strip():
        query = query.filter(SalesOrderItem.part_no.contains(part_no.strip(), autoescape=True))

    rows = query.order_by(
        SalesOrderMaster.order_date.asc(),
        SalesOrderMaster.customer_name.asc(),
        SalesOrderItem.part_no.asc(),
        SalesOrderItem.id.asc(),
    ).all()

    result = []
    for item, order in rows:
        order_qty = float(item.order_qty or 0)
        shipped_qty = float(item.shipped_qty or 0)
        unsold_qty = max(order_qty - shipped_qty, 0.0)
        if unsold_qty <= 1e-9:
            continue
        result.append({
            "order_no": order.order_no,
            "order_date": order.order_date,
            "customer_id": order.customer_id,
            "customer_name": order.customer_name,
            "part_no": item.part_no,
            "part_name": item.part_name,
            "order_qty": order_qty,
            "shipped_qty": shipped_qty,
            "unsold_qty": unsold_qty,
            "unit": item.unit,
            "delivery_date": item.delivery_date or order.delivery_due_date,
            "note": item.note or order.note or "",
        })
    return {"items": result, "total": len(result)}


@router.get("/api/sales/shipping/open-items")
def shipping_open_items(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = (
        db.query(SalesOrderItem)
        .join(SalesOrderMaster, SalesOrderMaster.id == SalesOrderItem.order_id)
        .filter(
            SalesOrderMaster.status.in_(["ORDERED", "PARTIAL"]),
            SalesOrderItem.status.in_(["WAITING", "PARTIAL"]),
            SalesOrderItem.shipped_qty < SalesOrderItem.order_qty,
        )
        .order_by(SalesOrderMaster.delivery_due_date.asc(), SalesOrderMaster.order_date.asc(), SalesOrderItem.id.asc())
        .all()
    )
    return [{
        "id": x.id,
        "order_id": x.order_id,
        "order_no": x.order.order_no,
        "customer_name": x.order.customer_name,
        "part_no": x.part_no,
        "part_name": x.part_name,
        "order_qty": x.order_qty,
        "shipped_qty": x.shipped_qty,
        "remaining_qty": max(float(x.order_qty or 0) - float(x.shipped_qty or 0), 0.0),
        "unit": x.unit,
        "delivery_date": x.delivery_date,
    } for x in rows]


@router.get("/api/sales/shipping/waiting-boxes")
def shipping_waiting_boxes(
    sales_order_item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.query(SalesOrderItem).filter(SalesOrderItem.id == sales_order_item_id).first()
    if not item:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")

    rows = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .outerjoin(ShipmentBox, ShipmentBox.packing_box_id == PackingBox.id)
        .filter(
            PackingMaster.part_no == item.part_no,
            PackingMaster.status == "PACKED",
            ShipmentBox.id.is_(None),
        )
        # 출고대기LOT는 YYMMDD+01+순번이므로, 포장일자 안에서는 LOT 번호 자체를
        # 선입순 기준으로 사용합니다. id는 동일 LOT 정렬의 마지막 안전키입니다.
        .order_by(
            PackingMaster.packing_date.asc(),
            PackingBox.package_lot_no.asc(),
            PackingBox.id.asc(),
        )
        .all()
    )
    return [{
        "id": box.id,
        "package_lot_no": box.package_lot_no,
        "box_qty": box.box_qty,
        "packing_date": master.packing_date,
        "part_no": master.part_no,
        "part_name": master.part_name,
    } for box, master in rows]


@router.post("/api/sales/shipping")
def create_shipment(payload: ShipmentCreateInput, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    order_item = db.query(SalesOrderItem).filter(SalesOrderItem.id == payload.sales_order_item_id).first()
    if not order_item:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")
    if order_item.order.status not in ("ORDERED", "PARTIAL") or order_item.status not in ("WAITING", "PARTIAL"):
        raise HTTPException(409, "이미 출고 완료되었거나 출고할 수 없는 수주입니다.")

    box_ids = list(dict.fromkeys(payload.packing_box_ids))
    if len(box_ids) != len(payload.packing_box_ids):
        raise HTTPException(409, "동일 출고대기LOT가 중복 선택되었습니다.")

    rows = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .outerjoin(ShipmentBox, ShipmentBox.packing_box_id == PackingBox.id)
        .filter(PackingBox.id.in_(box_ids), ShipmentBox.id.is_(None))
        .all()
    )
    if len(rows) != len(box_ids):
        raise HTTPException(409, "이미 출고된 LOT가 포함되어 있습니다. 목록을 새로고침해 주세요.")
    if any(master.part_no != order_item.part_no or master.status != "PACKED" for _, master in rows):
        raise HTTPException(409, "수주 품번과 일치하지 않는 출고대기LOT가 포함되어 있습니다.")

    shipment_qty = sum(float(box.box_qty or 0) for box, _ in rows)
    remaining_qty = float(order_item.order_qty or 0) - float(order_item.shipped_qty or 0)
    if shipment_qty <= 0:
        raise HTTPException(409, "출고수량이 0입니다.")
    if shipment_qty > remaining_qty + 1e-9:
        raise HTTPException(409, f"수주잔량 {remaining_qty:g}보다 선택 LOT 수량 {shipment_qty:g}이 큽니다.")

    order = order_item.order
    shipment = ShipmentMaster(
        shipment_no=_next_no(db, ShipmentMaster, ShipmentMaster.shipment_no, "SH", payload.shipment_date),
        shipment_date=payload.shipment_date,
        sales_order_id=order.id,
        customer_id=order.customer_id,
        customer_name=order.customer_name,
        status="CONFIRMED",
        note=(payload.note or "").strip() or None,
        created_by=_username(current_user),
    )
    db.add(shipment)
    db.flush()

    shipment_item = ShipmentItem(
        shipment_id=shipment.id,
        sales_order_item_id=order_item.id,
        part_no=order_item.part_no,
        shipped_qty=shipment_qty,
        unit=order_item.unit,
    )
    db.add(shipment_item)
    db.flush()

    for box, _ in rows:
        db.add(ShipmentBox(
            shipment_item_id=shipment_item.id,
            packing_box_id=box.id,
            package_lot_no=box.package_lot_no,
            shipped_qty=float(box.box_qty or 0),
        ))

    order_item.shipped_qty = float(order_item.shipped_qty or 0) + shipment_qty
    _sync_order_status(order)
    db.commit()

    return {
        "shipment_no": shipment.shipment_no,
        "shipped_qty": shipment_qty,
        "package_lots": [box.package_lot_no for box, _ in rows],
        "message": "출고 처리가 완료되었습니다.",
    }


@router.get("/api/sales/shipping/records")
def shipment_records(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    rows = db.query(ShipmentMaster).order_by(ShipmentMaster.shipment_date.desc(), ShipmentMaster.id.desc()).limit(500).all()
    return [{
        "shipment_no": x.shipment_no,
        "shipment_date": x.shipment_date,
        "order_no": x.items[0].sales_order_item.order.order_no if x.items else "",
        "customer_name": x.customer_name,
        "status": x.status,
        "items": [{
            "part_no": i.part_no,
            "shipped_qty": i.shipped_qty,
            "unit": i.unit,
            "package_lots": [b.package_lot_no for b in i.boxes],
        } for i in x.items],
    } for x in rows]
