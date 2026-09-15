from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel
from models.packing import PackingBox, PackingMaster
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentBox, ShipmentItem, ShipmentMaster

router = APIRouter(tags=["Sales Shipping Entry"])
templates = Jinja2Templates(directory="templates")


class ShipmentScanInput(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    lot_no: str = Field(min_length=1, max_length=60)
    selected_box_ids: list[int] = Field(default_factory=list)


class ShipmentAllocationInput(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    packing_box_ids: list[int] = Field(min_length=1)


class ShipmentOrderCreateInput(BaseModel):
    shipment_date: str = Field(min_length=10, max_length=10)
    sales_order_id: Optional[int] = Field(default=None, gt=0)  # 구버전 클라이언트 호환용
    items: list[ShipmentAllocationInput] = Field(min_length=1)
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
    for item in order.items:
        shipped = float(item.shipped_qty or 0)
        ordered = float(item.order_qty or 0)
        if shipped >= ordered - 1e-9:
            item.status = "COMPLETED"
        elif shipped > 1e-9:
            item.status = "PARTIAL"
        else:
            item.status = "WAITING"

    if order.items and all(float(x.shipped_qty or 0) >= float(x.order_qty or 0) - 1e-9 for x in order.items):
        order.status = "COMPLETED"
    elif any(float(x.shipped_qty or 0) > 1e-9 for x in order.items):
        order.status = "PARTIAL"
    else:
        order.status = "ORDERED"


def _waiting_rows(db: Session, part_no: str):
    return (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .outerjoin(ShipmentBox, ShipmentBox.packing_box_id == PackingBox.id)
        .filter(
            PackingMaster.part_no == part_no,
            PackingMaster.status == "PACKED",
            ShipmentBox.id.is_(None),
        )
        .order_by(
            PackingMaster.packing_date.asc(),
            PackingBox.package_lot_no.asc(),
            PackingBox.id.asc(),
        )
        .all()
    )


def _item_pack_qty(master: ItemMasterModel | None) -> int:
    if not master:
        return 0
    return int(master.moq or 0) or int(master.snp or 0) or 0


@router.get("/sales/shipping", response_class=HTMLResponse)
def shipping_entry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="sales_shipping.html",
        context={"request": request, "user": current_user},
    )


@router.get("/api/sales/shipping-entry/open-orders")
def open_orders(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    orders = (
        db.query(SalesOrderMaster)
        .filter(SalesOrderMaster.status.in_(["ORDERED", "PARTIAL"]))
        .order_by(SalesOrderMaster.delivery_due_date.asc(), SalesOrderMaster.order_date.asc(), SalesOrderMaster.id.asc())
        .all()
    )

    result = []
    for order in orders:
        open_items = [
            item for item in order.items
            if float(item.shipped_qty or 0) < float(item.order_qty or 0) - 1e-9
        ]
        if not open_items:
            continue

        part_nos = [item.part_no for item in open_items]
        masters = db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(part_nos)).all()
        master_map = {row.part_no: row for row in masters}

        items = []
        for item in open_items:
            master = master_map.get(item.part_no)
            waiting = _waiting_rows(db, item.part_no)
            remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)
            items.append({
                "id": item.id,
                "order_id": order.id,
                "order_no": order.order_no,
                "part_no": item.part_no,
                "part_name": item.part_name or (master.part_name if master else ""),
                "unit": item.unit or "EA",
                "order_qty": float(item.order_qty or 0),
                "shipped_qty": float(item.shipped_qty or 0),
                "remaining_qty": remaining_qty,
                "delivery_date": item.delivery_date or order.delivery_due_date,
                "moq": int(master.moq or 0) if master else 0,
                "pack_qty": _item_pack_qty(master),
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
            "note": order.note or "",
            "items": items,
        })

    return result


@router.get("/api/sales/shipping-entry/waiting-boxes")
def waiting_boxes(
    sales_order_item_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.get(SalesOrderItem, sales_order_item_id)
    if not item:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")

    rows = _waiting_rows(db, item.part_no)
    return [{
        "id": box.id,
        "package_lot_no": box.package_lot_no,
        "box_qty": float(box.box_qty or 0),
        "packing_date": master.packing_date,
        "part_no": master.part_no,
        "part_name": master.part_name,
        "fifo_order": index + 1,
    } for index, (box, master) in enumerate(rows)]


@router.post("/api/sales/shipping-entry/scan")
def scan_waiting_lot(
    payload: ShipmentScanInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.get(SalesOrderItem, payload.sales_order_item_id)
    if not item:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")
    if item.status not in ("WAITING", "PARTIAL") or item.order.status not in ("ORDERED", "PARTIAL"):
        raise HTTPException(409, "이미 출고 완료되었거나 출고할 수 없는 수주 품목입니다.")

    waiting = _waiting_rows(db, item.part_no)
    waiting_ids = [box.id for box, _ in waiting]
    selected_ids = list(dict.fromkeys(payload.selected_box_ids))
    if len(selected_ids) != len(payload.selected_box_ids):
        raise HTTPException(409, "동일 LOT가 중복 배정되어 있습니다.")

    if selected_ids:
        expected_prefix = waiting_ids[:len(selected_ids)]
        if selected_ids != expected_prefix:
            raise HTTPException(409, "LOT 배정 순서가 선입선출 기준과 일치하지 않습니다. 배정을 초기화해 주세요.")

    next_index = len(selected_ids)
    if next_index >= len(waiting):
        raise HTTPException(409, "추가로 출고 가능한 출고대기LOT가 없습니다.")

    expected_box, expected_master = waiting[next_index]
    scanned = payload.lot_no.strip()
    if scanned.lower() != str(expected_box.package_lot_no or "").lower():
        matching = next((box for box, _ in waiting if str(box.package_lot_no or "").lower() == scanned.lower()), None)
        if matching:
            raise HTTPException(409, f"선입 LOT {expected_box.package_lot_no}를 먼저 스캔해야 합니다.")
        raise HTTPException(404, "해당 품번의 출고 가능한 출고대기LOT를 찾을 수 없습니다.")

    allocated_qty = sum(float(waiting[i][0].box_qty or 0) for i in range(next_index + 1))
    remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)
    if allocated_qty > remaining_qty + 1e-9:
        raise HTTPException(
            409,
            f"이 LOT를 추가하면 수주 잔량 {remaining_qty:g}보다 출고 배정수량 {allocated_qty:g}이 커집니다. 부분 박스 출고는 지원하지 않습니다.",
        )

    return {
        "id": expected_box.id,
        "package_lot_no": expected_box.package_lot_no,
        "box_qty": float(expected_box.box_qty or 0),
        "packing_date": expected_master.packing_date,
        "part_no": expected_master.part_no,
        "part_name": expected_master.part_name,
        "fifo_order": next_index + 1,
        "allocated_qty": allocated_qty,
        "remaining_qty": remaining_qty,
    }


@router.post("/api/sales/shipping-entry/confirm")
def confirm_shipment(
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

        if customer_id is None:
            customer_id = order.customer_id
            customer_name = order.customer_name
        elif order.customer_id != customer_id:
            raise HTTPException(409, "서로 다른 판매처의 수주는 한 건의 출고전표로 묶을 수 없습니다.")

        sales_items[sales_item.id] = sales_item
        orders[order.id] = order

    if not sales_items:
        raise HTTPException(409, "출고할 수주 품목이 없습니다.")

    # 같은 품번이 여러 수주에 걸쳐 있어도, 이번 출고전표 전체 기준으로 FIFO 앞쪽 LOT만 사용해야 합니다.
    part_selected_ids: dict[str, list[int]] = {}
    used_box_ids: set[int] = set()
    for allocation in payload.items:
        sales_item = sales_items[allocation.sales_order_item_id]
        box_ids = list(dict.fromkeys(allocation.packing_box_ids))
        if len(box_ids) != len(allocation.packing_box_ids):
            raise HTTPException(409, f"{sales_item.part_no}: 동일 LOT가 중복 선택되었습니다.")
        if any(box_id in used_box_ids for box_id in box_ids):
            raise HTTPException(409, "서로 다른 수주 품목에 동일 포장 LOT가 중복 배정되었습니다.")
        used_box_ids.update(box_ids)
        part_selected_ids.setdefault(sales_item.part_no, []).extend(box_ids)

    waiting_by_part: dict[str, list[tuple[PackingBox, PackingMaster]]] = {}
    for part_no, selected_ids in part_selected_ids.items():
        waiting = _waiting_rows(db, part_no)
        waiting_by_part[part_no] = waiting
        expected_ids = [box.id for box, _ in waiting[:len(selected_ids)]]
        if len(expected_ids) != len(selected_ids) or set(selected_ids) != set(expected_ids):
            first_expected = waiting[0][0].package_lot_no if waiting else "없음"
            raise HTTPException(409, f"{part_no}: 선입선출 위반입니다. 선입 LOT {first_expected}부터 필요한 수량만큼 배정해야 합니다.")

    validated = []
    for allocation in payload.items:
        sales_item = sales_items[allocation.sales_order_item_id]
        box_ids = list(dict.fromkeys(allocation.packing_box_ids))
        waiting = waiting_by_part.get(sales_item.part_no, [])
        row_map = {box.id: (box, master) for box, master in waiting}
        selected_rows = [row_map[box_id] for box_id in box_ids if box_id in row_map]
        if len(selected_rows) != len(box_ids):
            raise HTTPException(409, f"{sales_item.part_no}: 이미 출고된 LOT가 포함되어 있습니다. 다시 조회해 주세요.")

        shipment_qty = sum(float(box.box_qty or 0) for box, _ in selected_rows)
        remaining_qty = max(float(sales_item.order_qty or 0) - float(sales_item.shipped_qty or 0), 0.0)
        if shipment_qty <= 0:
            raise HTTPException(409, f"{sales_item.part_no}: 출고수량이 0입니다.")
        if shipment_qty > remaining_qty + 1e-9:
            raise HTTPException(409, f"{sales_item.order.order_no} / {sales_item.part_no}: 수주 잔량 {remaining_qty:g}보다 출고수량 {shipment_qty:g}이 큽니다. 부분 박스 출고는 지원하지 않습니다.")
        validated.append((sales_item, selected_rows, shipment_qty))

    if not validated:
        raise HTTPException(409, "출고할 LOT를 배정해 주세요.")

    ordered_orders = sorted(orders.values(), key=lambda x: x.id)
    primary_order = ordered_orders[0]
    shipment = ShipmentMaster(
        shipment_no=_next_no(db, ShipmentMaster, ShipmentMaster.shipment_no, "SH", payload.shipment_date),
        shipment_date=payload.shipment_date,
        # 기존 DB/기능 호환을 위해 대표 수주 1건을 보존한다. 실제 추적은 ShipmentItem.sales_order_item_id 기준이다.
        sales_order_id=primary_order.id,
        customer_id=customer_id,
        customer_name=customer_name,
        status="CONFIRMED",
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
        "message": "출고 처리가 완료되었습니다.",
    }
