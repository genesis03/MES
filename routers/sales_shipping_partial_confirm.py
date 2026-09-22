from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.packing import PackingBox, PackingMaster
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentBox, ShipmentItem, ShipmentMaster
from routers.sales_shipping_entry import _next_no, _sync_order_status, _username, _waiting_rows

router = APIRouter(tags=["Sales Shipping Partial Confirm"])


class ShipmentAllocationInput(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    packing_box_ids: list[int] = Field(min_length=1)
    requested_qty: float = Field(gt=0)


class ShipmentOrderCreateInput(BaseModel):
    shipment_date: str = Field(min_length=10, max_length=10)
    sales_order_id: Optional[int] = Field(default=None, gt=0)
    items: list[ShipmentAllocationInput] = Field(min_length=1)
    note: Optional[str] = Field(default=None, max_length=1000)


@router.post("/api/sales/shipping-entry/confirm")
def confirm_shipment_with_requested_qty(
    payload: ShipmentOrderCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    allocation_item_ids = [row.sales_order_item_id for row in payload.items]
    if len(set(allocation_item_ids)) != len(allocation_item_ids):
        raise HTTPException(409, "동일 수주 품목이 중복 배정되었습니다.")

    sales_items: dict[int, SalesOrderItem] = {}
    orders: dict[int, SalesOrderMaster] = {}
    customer_id = None
    customer_name = None

    for allocation in payload.items:
        sales_item = db.get(SalesOrderItem, allocation.sales_order_item_id)
        if not sales_item or not sales_item.order:
            raise HTTPException(409, "수주 품목 또는 수주 정보를 찾을 수 없습니다.")
        order = sales_item.order
        if sales_item.status not in ("WAITING", "PARTIAL") or order.status not in ("ORDERED", "PARTIAL"):
            raise HTTPException(409, f"{sales_item.part_no}: 이미 출고 완료되었거나 출고할 수 없는 수주 품목입니다.")
        remaining_qty = max(float(sales_item.order_qty or 0) - float(sales_item.shipped_qty or 0), 0.0)
        if allocation.requested_qty > remaining_qty + 1e-9:
            raise HTTPException(409, f"{order.order_no} / {sales_item.part_no}: 금회 출고수량 {allocation.requested_qty:g}은 미출고 잔량 {remaining_qty:g}보다 클 수 없습니다.")
        if customer_id is None:
            customer_id = order.customer_id
            customer_name = order.customer_name
        elif order.customer_id != customer_id:
            raise HTTPException(409, "서로 다른 판매처의 수주는 한 건의 출고전표로 묶을 수 없습니다.")
        sales_items[sales_item.id] = sales_item
        orders[order.id] = order

    used_box_ids: set[int] = set()
    validated = []
    fifo_exception_reasons: list[str] = []
    waiting_cache: dict[str, list[tuple[PackingBox, PackingMaster]]] = {}

    for allocation in payload.items:
        sales_item = sales_items[allocation.sales_order_item_id]
        order = sales_item.order
        order_type = str(getattr(order, "order_type", None) or "NORMAL").upper()
        box_ids = list(dict.fromkeys(allocation.packing_box_ids))
        if len(box_ids) != len(allocation.packing_box_ids):
            raise HTTPException(409, f"{sales_item.part_no}: 동일 LOT가 중복 선택되었습니다.")
        if any(box_id in used_box_ids for box_id in box_ids):
            raise HTTPException(409, "서로 다른 수주 품목에 동일 포장 LOT가 중복 배정되었습니다.")

        waiting = waiting_cache.setdefault(sales_item.part_no, _waiting_rows(db, sales_item.item_id))
        row_map = {box.id: (box, master) for box, master in waiting}
        selected_rows = [row_map[box_id] for box_id in box_ids if box_id in row_map]
        if len(selected_rows) != len(box_ids):
            raise HTTPException(409, f"{sales_item.part_no}: 이미 출고된 LOT가 포함되어 있습니다. 다시 조회해 주세요.")

        shipment_qty = sum(float(box.box_qty or 0) for box, _ in selected_rows)
        remaining_qty = max(float(sales_item.order_qty or 0) - float(sales_item.shipped_qty or 0), 0.0)
        if shipment_qty <= 0:
            raise HTTPException(409, f"{sales_item.part_no}: 출고수량이 0입니다.")
        if shipment_qty > remaining_qty + 1e-9:
            raise HTTPException(409, f"{order.order_no} / {sales_item.part_no}: 수주 잔량 {remaining_qty:g}보다 출고수량 {shipment_qty:g}이 큽니다.")
        if abs(shipment_qty - allocation.requested_qty) > 1e-9:
            raise HTTPException(409, f"{order.order_no} / {sales_item.part_no}: 금회 출고 지정수량 {allocation.requested_qty:g}과 실제 포장 LOT 수량 합계 {shipment_qty:g}이 일치하지 않습니다.")

        waiting_ids = [box.id for box, _ in waiting if box.id not in used_box_ids]
        expected_ids = waiting_ids[:len(box_ids)]
        is_fifo = set(box_ids) == set(expected_ids)
        if order_type == "NORMAL" and not is_fifo:
            first_expected = next((box.package_lot_no for box, _ in waiting if box.id not in used_box_ids), "없음")
            raise HTTPException(409, f"{order.order_no} / {sales_item.part_no}: 정상 수주는 선입 LOT {first_expected}부터 출고해야 합니다.")
        if order_type in ("SAMPLE", "DEVELOPMENT") and not is_fifo:
            selected_lots = ", ".join(str(box.package_lot_no) for box, _ in selected_rows)
            label = "샘플" if order_type == "SAMPLE" else "개발"
            fifo_exception_reasons.append(f"{order.order_no}/{sales_item.part_no} {label} 출고: {selected_lots}")

        used_box_ids.update(box_ids)
        validated.append((sales_item, selected_rows, shipment_qty))

    if not validated:
        raise HTTPException(409, "출고할 LOT를 배정해 주세요.")

    ordered_orders = sorted(orders.values(), key=lambda x: x.id)
    primary_order = ordered_orders[0]
    shipment = ShipmentMaster(
        shipment_no=_next_no(db, ShipmentMaster, ShipmentMaster.shipment_no, "SH", payload.shipment_date),
        shipment_date=payload.shipment_date,
        sales_order_id=primary_order.id,
        customer_id=customer_id,
        customer_name=customer_name,
        status="CONFIRMED",
        fifo_exception="Y" if fifo_exception_reasons else "N",
        fifo_exception_reason=" | ".join(fifo_exception_reasons) or None,
        note=(payload.note or "").strip() or None,
        created_by=_username(current_user),
    )
    db.add(shipment)
    db.flush()

    total_qty = 0.0
    for sales_item, selected_rows, shipment_qty in validated:
        shipment_item = ShipmentItem(
            shipment_id=shipment.id,
            sales_order_item_id=sales_item.id,
            item_id=sales_item.item_id,
            part_no=sales_item.part_no,
            shipped_qty=shipment_qty,
            unit=sales_item.unit or "EA",
        )
        db.add(shipment_item)
        db.flush()
        for box, _ in selected_rows:
            db.add(ShipmentBox(
                shipment_item_id=shipment_item.id,
                packing_box_id=box.id,
                package_lot_no=box.package_lot_no,
                shipped_qty=float(box.box_qty or 0),
            ))
        sales_item.shipped_qty = float(sales_item.shipped_qty or 0) + shipment_qty
        total_qty += shipment_qty

    for order in orders.values():
        _sync_order_status(order)
    db.commit()

    return {
        "shipment_id": shipment.id,
        "shipment_no": shipment.shipment_no,
        "shipment_date": shipment.shipment_date,
        "total_qty": total_qty,
        "item_count": len(validated),
        "order_count": len(orders),
        "order_nos": [row.order_no for row in ordered_orders],
        "fifo_exception": bool(fifo_exception_reasons),
        "message": "출고 처리가 완료되었습니다.",
    }
