from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel
from models.packing import PackingBox, PackingMaster
from models.partner import Partner
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentBox
from routers.sales_shipping_entry import _next_no, _username

router = APIRouter(tags=["Sales Order Policy"])

ORDER_TYPES = {"NORMAL", "SAMPLE", "DEVELOPMENT"}
TRANSACTION_TYPES = {"PAID", "FREE"}


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


@router.post("/api/sales/orders")
def create_sales_order_with_policy(
    payload: SalesOrderCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order_type = payload.order_type.strip().upper()
    transaction_type = payload.transaction_type.strip().upper()
    if order_type not in ORDER_TYPES:
        raise HTTPException(409, "수주구분은 정상/샘플/개발 중에서 선택해 주세요.")
    if transaction_type not in TRANSACTION_TYPES:
        raise HTTPException(409, "거래구분은 유상/무상 중에서 선택해 주세요.")

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
        "order_type": x.order_type or "NORMAL",
        "transaction_type": x.transaction_type or "PAID",
        "manager_name": x.manager_name,
        "note": x.note,
        "items": [{
            "id": i.id,
            "item_id": i.item_id,
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
