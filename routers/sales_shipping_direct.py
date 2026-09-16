from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentDirectLot, ShipmentItem, ShipmentMaster
from routers.packing import _lots
from routers.sales_shipping_entry import _next_no, _sync_order_status, _username

router = APIRouter(tags=["Sales Shipping Direct"])

DIRECT_ORDER_TYPES = {"SAMPLE", "DEVELOPMENT"}


class DirectLotSelection(BaseModel):
    production_lot_id: int = Field(gt=0)
    qty: float = Field(gt=0)


class DirectScanInput(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    lot_no: str = Field(min_length=1, max_length=100)
    selected_lots: list[DirectLotSelection] = Field(default_factory=list)
    requested_qty: float = Field(gt=0)


class DirectShipmentAllocation(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    requested_qty: float = Field(gt=0)
    direct_lots: list[DirectLotSelection] = Field(min_length=1)


class DirectShipmentCreateInput(BaseModel):
    shipment_date: str = Field(min_length=10, max_length=10)
    items: list[DirectShipmentAllocation] = Field(min_length=1)
    note: Optional[str] = Field(default=None, max_length=1000)


def _direct_rows(db: Session, item: SalesOrderItem):
    rows = []
    for row in _lots(db, item.part_no):
        lot = row["lot"]
        available = float(row["available"] or 0)
        if available <= 1e-9:
            continue
        rows.append((lot, row["source_part_no"], row["source_part_name"], available))
    return rows


@router.get("/api/sales/shipping-entry/direct-lots")
def direct_lots(
    sales_order_item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.get(SalesOrderItem, sales_order_item_id)
    if not item or not item.order:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")
    if str(item.order.order_type or "NORMAL").upper() not in DIRECT_ORDER_TYPES:
        raise HTTPException(409, "양산 수주는 생산 LOT 직출고를 사용할 수 없습니다.")

    return [{
        "id": lot.id,
        "lot_no": lot.lot_no,
        "source_part_no": source_part_no,
        "source_part_name": source_part_name,
        "available_qty": available,
        "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M:%S") if lot.created_at else "",
    } for lot, source_part_no, source_part_name, available in _direct_rows(db, item)]


@router.post("/api/sales/shipping-entry/direct-scan")
def direct_scan(
    payload: DirectScanInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.get(SalesOrderItem, payload.sales_order_item_id)
    if not item or not item.order:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")
    order_type = str(item.order.order_type or "NORMAL").upper()
    if order_type not in DIRECT_ORDER_TYPES:
        raise HTTPException(409, "양산 수주는 포장 출고대기 LOT를 통해 출고해야 합니다.")
    if item.status not in ("WAITING", "PARTIAL") or item.order.status not in ("ORDERED", "PARTIAL"):
        raise HTTPException(409, "이미 출고 완료되었거나 출고할 수 없는 수주 품목입니다.")

    remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)
    if payload.requested_qty > remaining_qty + 1e-9:
        raise HTTPException(409, f"금회 출고수량 {payload.requested_qty:g}은 미출고 잔량 {remaining_qty:g}보다 클 수 없습니다.")

    available_rows = _direct_rows(db, item)
    row_map = {lot.id: (lot, source_part_no, source_part_name, available) for lot, source_part_no, source_part_name, available in available_rows}
    selected_ids = [row.production_lot_id for row in payload.selected_lots]
    if len(set(selected_ids)) != len(selected_ids):
        raise HTTPException(409, "동일 생산 LOT가 중복 배정되었습니다.")

    allocations = []
    allocated_qty = 0.0
    for selected in payload.selected_lots:
        current = row_map.get(selected.production_lot_id)
        if not current:
            raise HTTPException(409, "기존에 배정한 생산 LOT의 가용수량이 변경되었습니다. 배정을 초기화해 주세요.")
        lot, source_part_no, source_part_name, available = current
        if selected.qty > available + 1e-9:
            raise HTTPException(409, f"{lot.lot_no}: 가용수량 {available:g}보다 배정수량 {selected.qty:g}이 큽니다.")
        allocations.append({
            "id": lot.id,
            "production_lot_id": lot.id,
            "package_lot_no": lot.lot_no,
            "lot_no": lot.lot_no,
            "box_qty": float(selected.qty),
            "allocated_qty": float(selected.qty),
            "packing_date": lot.created_at.strftime("%Y-%m-%d") if lot.created_at else "",
            "source_part_no": source_part_no,
            "source_part_name": source_part_name,
            "available_qty": available,
            "direct": True,
        })
        allocated_qty += float(selected.qty)

    scanned = payload.lot_no.strip().lower()
    scanned_row = next((row for row in available_rows if str(row[0].lot_no or "").lower() == scanned), None)
    if not scanned_row:
        raise HTTPException(404, "포장되지 않은 사용 가능한 생산 LOT를 찾을 수 없습니다.")
    lot, source_part_no, source_part_name, available = scanned_row
    if lot.id in selected_ids:
        raise HTTPException(409, "이미 배정된 생산 LOT입니다.")

    need = payload.requested_qty - allocated_qty
    if need <= 1e-9:
        raise HTTPException(409, "금회 출고수량 배정이 이미 완료되었습니다.")
    qty = min(available, need)
    if qty <= 1e-9:
        raise HTTPException(409, "해당 생산 LOT의 출고 가능수량이 없습니다.")

    allocations.append({
        "id": lot.id,
        "production_lot_id": lot.id,
        "package_lot_no": lot.lot_no,
        "lot_no": lot.lot_no,
        "box_qty": qty,
        "allocated_qty": qty,
        "packing_date": lot.created_at.strftime("%Y-%m-%d") if lot.created_at else "",
        "source_part_no": source_part_no,
        "source_part_name": source_part_name,
        "available_qty": available,
        "direct": True,
    })
    allocated_qty += qty

    return {
        "allocations": allocations,
        "allocated_qty": allocated_qty,
        "requested_qty": payload.requested_qty,
        "remaining_qty": remaining_qty,
        "is_full_allocated": abs(allocated_qty - payload.requested_qty) <= 1e-9,
        "shipment_mode": "DIRECT",
    }


@router.post("/api/sales/shipping-entry/direct-confirm")
def direct_confirm(
    payload: DirectShipmentCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item_ids = [row.sales_order_item_id for row in payload.items]
    if len(set(item_ids)) != len(item_ids):
        raise HTTPException(409, "동일 수주 품목이 중복 배정되었습니다.")

    sales_items: dict[int, SalesOrderItem] = {}
    orders: dict[int, SalesOrderMaster] = {}
    customer_id = None
    customer_name = None
    validated = []
    planned_by_lot: dict[int, float] = {}

    for allocation in payload.items:
        item = db.get(SalesOrderItem, allocation.sales_order_item_id)
        if not item or not item.order:
            raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")
        order = item.order
        order_type = str(order.order_type or "NORMAL").upper()
        if order_type not in DIRECT_ORDER_TYPES:
            raise HTTPException(409, f"{order.order_no}: 양산 수주는 생산 LOT 직출고 대상이 아닙니다.")
        if item.status not in ("WAITING", "PARTIAL") or order.status not in ("ORDERED", "PARTIAL"):
            raise HTTPException(409, f"{order.order_no} / {item.part_no}: 출고할 수 없는 수주입니다.")

        remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)
        if allocation.requested_qty > remaining_qty + 1e-9:
            raise HTTPException(409, f"{order.order_no} / {item.part_no}: 금회 출고수량이 미출고 잔량보다 큽니다.")

        if customer_id is None:
            customer_id = order.customer_id
            customer_name = order.customer_name
        elif order.customer_id != customer_id:
            raise HTTPException(409, "서로 다른 판매처의 수주는 한 건의 출고전표로 묶을 수 없습니다.")

        available_rows = _direct_rows(db, item)
        row_map = {lot.id: (lot, source_part_no, available) for lot, source_part_no, _, available in available_rows}
        direct_ids = [row.production_lot_id for row in allocation.direct_lots]
        if len(set(direct_ids)) != len(direct_ids):
            raise HTTPException(409, f"{item.part_no}: 동일 생산 LOT가 중복 배정되었습니다.")

        selected = []
        total = 0.0
        for direct in allocation.direct_lots:
            current = row_map.get(direct.production_lot_id)
            if not current:
                raise HTTPException(409, f"{item.part_no}: 생산 LOT 가용수량이 변경되었습니다. 다시 배정해 주세요.")
            lot, source_part_no, available = current
            already_planned = planned_by_lot.get(lot.id, 0.0)
            effective_available = max(available - already_planned, 0.0)
            if direct.qty > effective_available + 1e-9:
                raise HTTPException(409, f"{lot.lot_no}: 이번 출고전표 내 다른 품목 배정까지 포함한 가용수량 {effective_available:g}보다 출고수량 {direct.qty:g}이 큽니다.")
            planned_by_lot[lot.id] = already_planned + float(direct.qty)
            selected.append((lot, source_part_no, float(direct.qty)))
            total += float(direct.qty)

        if abs(total - allocation.requested_qty) > 1e-9:
            raise HTTPException(409, f"{order.order_no} / {item.part_no}: 금회 출고수량과 생산 LOT 배정수량이 일치하지 않습니다.")

        sales_items[item.id] = item
        orders[order.id] = order
        validated.append((item, selected, total))

    ordered_orders = sorted(orders.values(), key=lambda x: x.id)
    primary_order = ordered_orders[0]
    shipment = ShipmentMaster(
        shipment_no=_next_no(db, ShipmentMaster, ShipmentMaster.shipment_no, "SH", payload.shipment_date),
        shipment_date=payload.shipment_date,
        sales_order_id=primary_order.id,
        customer_id=customer_id,
        customer_name=customer_name,
        status="CONFIRMED",
        fifo_exception="Y",
        fifo_exception_reason="샘플/개발 수주 포장 생략 생산 LOT 직출고",
        note=(payload.note or "").strip() or None,
        created_by=_username(current_user),
    )
    db.add(shipment)
    db.flush()

    total_qty = 0.0
    for item, selected, shipment_qty in validated:
        shipment_item = ShipmentItem(
            shipment_id=shipment.id,
            sales_order_item_id=item.id,
            part_no=item.part_no,
            shipped_qty=shipment_qty,
            unit=item.unit or "EA",
        )
        db.add(shipment_item)
        db.flush()

        for lot, source_part_no, qty in selected:
            db.add(ShipmentDirectLot(
                shipment_item_id=shipment_item.id,
                production_lot_id=lot.id,
                source_lot_no=lot.lot_no,
                source_part_no=source_part_no,
                shipped_qty=qty,
            ))
            # 기존 생산 LOT 가용수량 계산이 LotRelation 소비량을 이미 차감하므로
            # 직출고도 동일 원장에 기록하여 포장/후속공정에서 재사용되지 않게 한다.
            db.add(LotRelationModel(
                parent_lot_no=lot.lot_no,
                child_lot_no=f"SHIP:{shipment.shipment_no}:{shipment_item.id}:{lot.id}",
                process_code="SHIP_DIRECT",
                consumed_qty=qty,
            ))

        item.shipped_qty = float(item.shipped_qty or 0) + shipment_qty
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
        "shipment_mode": "DIRECT",
        "message": "샘플/개발 생산 LOT 직출고 처리가 완료되었습니다.",
    }
