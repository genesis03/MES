from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel
from models.packing import PackingBox, PackingMaster
from models.partner import Partner
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentBox, ShipmentItem, ShipmentMaster
from routers.sales_shipping_entry import _next_no, _username

router = APIRouter(tags=["Sales Order Policy"])

ORDER_TYPES = {"NORMAL", "SAMPLE", "DEVELOPMENT"}
TRANSACTION_TYPES = {"PAID", "FREE"}
ORDER_STATUSES = {"ORDERED", "PARTIAL", "COMPLETED", "CANCELLED"}


class SalesOrderItemInput(BaseModel):
    part_no: str = Field(min_length=1, max_length=50)
    order_qty: float = Field(gt=0)
    delivery_date: Optional[str] = Field(default=None, max_length=10)
    note: Optional[str] = Field(default=None, max_length=500)


class SalesOrderCreateInput(BaseModel):
    order_date: str = Field(min_length=10, max_length=10)
    delivery_due_date: Optional[str] = Field(default=None, max_length=10)
    customer_id: int = Field(gt=0)
    order_type: str = Field(default="NORMAL", max_length=20)
    transaction_type: str = Field(default="PAID", max_length=20)
    manager_name: Optional[str] = Field(default=None, max_length=50)
    note: Optional[str] = Field(default=None, max_length=1000)
    items: list[SalesOrderItemInput] = Field(min_length=1)


class SelectedOrderIds(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


def _waiting_rows(db: Session, item_id: int):
    return (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .outerjoin(ShipmentBox, ShipmentBox.packing_box_id == PackingBox.id)
        .filter(
            PackingMaster.item_id == item_id,
            PackingMaster.status == "PACKED",
            ShipmentBox.id.is_(None),
        )
        .order_by(PackingMaster.packing_date.asc(), PackingBox.package_lot_no.asc(), PackingBox.id.asc())
        .all()
    )


def _pack_qty(master: ItemMasterModel | None) -> int:
    if not master:
        return 0
    return int(master.moq or 0) or int(master.snp or 0) or 0


def _serialize_order(order: SalesOrderMaster) -> dict:
    has_shipment = any(float(item.shipped_qty or 0) > 1e-9 for item in order.items)
    return {
        "id": order.id,
        "order_no": order.order_no,
        "order_date": order.order_date,
        "delivery_due_date": order.delivery_due_date,
        "customer_id": order.customer_id,
        "customer_name": order.customer_name,
        "status": order.status,
        "order_type": order.order_type or "NORMAL",
        "transaction_type": order.transaction_type or "PAID",
        "manager_name": order.manager_name or "",
        "note": order.note or "",
        "editable": order.status == "ORDERED" and not has_shipment,
        "items": [{
            "id": item.id,
            "item_id": item.item_id,
            "part_no": item.part_no,
            "part_name": item.part_name or "",
            "order_qty": float(item.order_qty or 0),
            "shipped_qty": float(item.shipped_qty or 0),
            "remaining_qty": max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0),
            "unit": item.unit or "EA",
            "delivery_date": item.delivery_date,
            "note": item.note or "",
            "status": item.status,
        } for item in order.items],
    }


def _validate_order_payload(db: Session, payload: SalesOrderCreateInput):
    order_type = payload.order_type.strip().upper()
    transaction_type = payload.transaction_type.strip().upper()

    if order_type not in ORDER_TYPES:
        raise HTTPException(409, "수주구분은 양산/샘플/개발 중에서 선택해 주세요.")
    if transaction_type not in TRANSACTION_TYPES:
        raise HTTPException(409, "거래구분은 유상/무상 중에서 선택해 주세요.")

    customer = db.query(Partner).filter(
        Partner.id == payload.customer_id,
        Partner.is_active == "Y",
        Partner.partner_type.in_(["CUSTOMER", "BOTH"]),
    ).first()
    if not customer:
        raise HTTPException(404, "사용 가능한 판매처를 찾을 수 없습니다.")

    part_nos = [row.part_no.strip() for row in payload.items]
    item_keys = [
        (row.part_no.strip(), row.delivery_date or payload.delivery_due_date or "")
        for row in payload.items
    ]
    if len(set(item_keys)) != len(item_keys):
        raise HTTPException(409, "동일 품번과 동일 납기일은 한 수주에 중복 입력할 수 없습니다.")

    item_rows = db.query(ItemMasterModel).filter(
        ItemMasterModel.part_no.in_(part_nos),
        ItemMasterModel.is_active == "Y",
        ItemMasterModel.material_type.in_(["SEMI", "FINISHED"]),
    ).all()
    item_map = {row.part_no: row for row in item_rows}
    missing = [part_no for part_no in part_nos if part_no not in item_map]
    if missing:
        raise HTTPException(404, f"수주 가능한 완제품/반제품을 찾을 수 없습니다: {', '.join(missing)}")

    return order_type, transaction_type, customer, item_map


def _has_shipment_history(db: Session, order: SalesOrderMaster) -> bool:
    return bool(
        db.query(ShipmentMaster.id)
        .filter(ShipmentMaster.sales_order_id == order.id)
        .first()
        or db.query(ShipmentItem.id)
        .join(SalesOrderItem, SalesOrderItem.id == ShipmentItem.sales_order_item_id)
        .filter(SalesOrderItem.order_id == order.id)
        .first()
        or any(float(item.shipped_qty or 0) > 1e-9 for item in order.items)
    )


@router.post("/api/sales/orders/delete-selected")
def delete_selected_sales_orders(
    payload: SelectedOrderIds,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ids = sorted(set(payload.ids))
    orders = db.query(SalesOrderMaster).filter(SalesOrderMaster.id.in_(ids)).all()
    found = {order.id for order in orders}
    missing = [order_id for order_id in ids if order_id not in found]
    if missing:
        raise HTTPException(404, "수주를 찾을 수 없습니다: " + ", ".join(map(str, missing)))

    blocked = [order.order_no for order in orders if order.status != "ORDERED" or _has_shipment_history(db, order)]
    if blocked:
        raise HTTPException(409, "출고 이력이 있거나 삭제할 수 없는 상태의 수주가 포함되어 있습니다: " + ", ".join(blocked))

    for order in orders:
        db.delete(order)
    db.commit()
    return {"deleted": len(orders), "message": f"수주 {len(orders)}건을 삭제했습니다."}


@router.get("/api/sales/orders/{order_id}")
def sales_order_detail(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.get(SalesOrderMaster, order_id)
    if order is None:
        raise HTTPException(404, "수주를 찾을 수 없습니다.")

    data = _serialize_order(order)
    if _has_shipment_history(db, order):
        data["editable"] = False
    return data


@router.put("/api/sales/orders/{order_id}")
def update_sales_order_with_policy(
    order_id: int,
    payload: SalesOrderCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.get(SalesOrderMaster, order_id)
    if order is None:
        raise HTTPException(404, "수주를 찾을 수 없습니다.")

    if _has_shipment_history(db, order) or order.status != "ORDERED":
        raise HTTPException(409, "출고 이력이 있는 수주는 수정할 수 없습니다.")

    order_type, transaction_type, customer, item_map = _validate_order_payload(db, payload)

    order.order_date = payload.order_date
    order.delivery_due_date = payload.delivery_due_date or None
    order.customer_id = customer.id
    order.customer_name = customer.partner_name
    order.order_type = order_type
    order.transaction_type = transaction_type
    order.manager_name = (payload.manager_name or "").strip() or None
    order.note = (payload.note or "").strip() or None

    order.items.clear()
    db.flush()
    for row in payload.items:
        master = item_map[row.part_no.strip()]
        order.items.append(SalesOrderItem(
            item_id=master.id,
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
    return {"id": order.id, "order_no": order.order_no, "message": "수주가 수정되었습니다."}


@router.post("/api/sales/orders")
def create_sales_order_with_policy(
    payload: SalesOrderCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order_type, transaction_type, customer, item_map = _validate_order_payload(db, payload)

    order = SalesOrderMaster(
        order_no=_next_no(db, SalesOrderMaster, SalesOrderMaster.order_no, "SO", payload.order_date),
        order_date=payload.order_date,
        delivery_due_date=payload.delivery_due_date or None,
        customer_id=customer.id,
        customer_name=customer.partner_name,
        status="ORDERED",
        order_type=order_type,
        transaction_type=transaction_type,
        manager_name=(payload.manager_name or "").strip() or None,
        note=(payload.note or "").strip() or None,
        created_by=_username(current_user),
    )
    db.add(order)
    db.flush()

    for row in payload.items:
        master = item_map[row.part_no.strip()]
        order.items.append(SalesOrderItem(
            item_id=master.id,
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
def sales_orders_with_policy(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    customer_id: Optional[int] = Query(None, gt=0),
    order_no: Optional[str] = Query(None, max_length=30),
    status: Optional[str] = Query(None, max_length=20),
    item_id: Optional[list[int]] = Query(None),
    limit: int = Query(1000, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(SalesOrderMaster)

    if start_date:
        query = query.filter(SalesOrderMaster.order_date >= start_date)
    if end_date:
        query = query.filter(SalesOrderMaster.order_date <= end_date)
    if customer_id:
        query = query.filter(SalesOrderMaster.customer_id == customer_id)
    if order_no and order_no.strip():
        query = query.filter(SalesOrderMaster.order_no.contains(order_no.strip(), autoescape=True))
    if status:
        normalized_status = status.strip().upper()
        if normalized_status not in ORDER_STATUSES:
            raise HTTPException(422, "지원하지 않는 수주 상태입니다.")
        query = query.filter(SalesOrderMaster.status == normalized_status)

    selected_item_ids = sorted({value for value in (item_id or []) if value > 0})
    if selected_item_ids:
        query = query.filter(
            SalesOrderMaster.items.any(SalesOrderItem.item_id.in_(selected_item_ids))
        )

    rows = (
        query.order_by(SalesOrderMaster.order_date.desc(), SalesOrderMaster.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_order(order) for order in rows]


@router.get("/api/sales/shipping-entry/open-orders")
def open_orders_with_policy(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    orders = (
        db.query(SalesOrderMaster)
        .filter(SalesOrderMaster.status.in_(["ORDERED", "PARTIAL"]))
        .order_by(SalesOrderMaster.delivery_due_date.asc(), SalesOrderMaster.order_date.asc(), SalesOrderMaster.id.asc())
        .all()
    )

    result = []
    for order in orders:
        open_items = [item for item in order.items if float(item.shipped_qty or 0) < float(item.order_qty or 0) - 1e-9]
        if not open_items:
            continue
        item_ids = [item.item_id for item in open_items if item.item_id]
        masters = db.query(ItemMasterModel).filter(ItemMasterModel.id.in_(item_ids)).all() if item_ids else []
        master_map = {row.id: row for row in masters}
        items = []
        for item in open_items:
            master = master_map.get(item.item_id)
            waiting = _waiting_rows(db, item.item_id)
            remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)
            items.append({
                "id": item.id,
                "item_id": item.item_id,
                "order_id": order.id,
                "order_no": order.order_no,
                "order_type": order.order_type or "NORMAL",
                "transaction_type": order.transaction_type or "PAID",
                "part_no": master.part_no if master else item.part_no,
                "part_name": item.part_name or (master.part_name if master else ""),
                "unit": item.unit or "EA",
                "order_qty": float(item.order_qty or 0),
                "shipped_qty": float(item.shipped_qty or 0),
                "remaining_qty": remaining_qty,
                "delivery_date": item.delivery_date or order.delivery_due_date,
                "moq": int(master.moq or 0) if master else 0,
                "pack_qty": _pack_qty(master),
                "waiting_box_count": len(waiting),
                "waiting_qty": sum(float(box.box_qty or 0) for box, _ in waiting),
            })
        result.append({
            "id": order.id,
            "order_no": order.order_no,
            "order_date": order.order_date,
            "delivery_due_date": order.delivery_due_date,
            "customer_id": order.customer_id,
            "customer_name": order.customer_name,
            "manager_name": order.manager_name or "",
            "status": order.status,
            "order_type": order.order_type or "NORMAL",
            "transaction_type": order.transaction_type or "PAID",
            "note": order.note or "",
            "items": items,
        })
    return result
