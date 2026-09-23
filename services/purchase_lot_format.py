from decimal import Decimal
import math

from fastapi import HTTPException

from models.models import PurchaseInboundItem
import services.purchase_service as purchase_service


def _next_internal_lot(db, inbound_date: str, reserved: set[str]) -> str:
    date_text = (inbound_date or "").replace("-", "")
    if len(date_text) != 8 or not date_text.isdigit():
        raise HTTPException(422, "입고일자는 YYYY-MM-DD 형식이어야 합니다.")

    prefix = f"LR{date_text[2:]}"
    existing = {
        row[0]
        for row in db.query(PurchaseInboundItem.internal_lot_no)
        .filter(PurchaseInboundItem.internal_lot_no.like(prefix + "%"))
        .all()
        if row[0]
    }
    existing.update(reserved)

    for seq in range(1, 1000):
        lot_no = f"{prefix}{seq:03d}"
        if lot_no not in existing:
            reserved.add(lot_no)
            return lot_no

    raise HTTPException(409, f"{prefix}의 일일 내부 LOT 순번 001~999를 모두 사용했습니다.")


def confirm_saved_inbound_short_lot(db, master, preserve_lot=False):
    affected = {}
    reserved: set[str] = set()

    for item in master.items:
        po_item = purchase_service.linked_order_item(db, item, master)
        if po_item is not None:
            received = float(Decimal(str(po_item.received_qty)) + Decimal(str(item.inbound_qty)))
            if not math.isfinite(received):
                raise HTTPException(422, "누적 입고수량이 저장 가능한 범위를 초과했습니다.")
            if received > po_item.order_qty:
                raise HTTPException(422, f"입고수량이 발주수량을 초과합니다: {po_item.part_no}")
            po_item.received_qty = received
            affected[po_item.order.id] = po_item.order

        if not (preserve_lot and item.internal_lot_no):
            item.internal_lot_no = _next_internal_lot(db, master.inbound_date, reserved)

    for order in affected.values():
        purchase_service.refresh_order_status(order)
    master.status = "CONFIRMED"


def install_purchase_lot_format() -> None:
    purchase_service.confirm_saved_inbound = confirm_saved_inbound_short_lot
