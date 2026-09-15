from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.sales import SalesOrderItem
from routers.sales_shipping_entry import _waiting_rows

router = APIRouter(tags=["Sales Shipping FIFO Auto"])


class ShipmentScanAutoInput(BaseModel):
    sales_order_item_id: int = Field(gt=0)
    lot_no: str = Field(min_length=1, max_length=60)
    selected_box_ids: list[int] = Field(default_factory=list)
    reserved_box_ids: list[int] = Field(default_factory=list)


@router.post("/api/sales/shipping-entry/scan")
def scan_waiting_lot_auto(
    payload: ShipmentScanAutoInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    item = db.get(SalesOrderItem, payload.sales_order_item_id)
    if not item:
        raise HTTPException(404, "수주 품목을 찾을 수 없습니다.")
    if item.status not in ("WAITING", "PARTIAL") or item.order.status not in ("ORDERED", "PARTIAL"):
        raise HTTPException(409, "이미 출고 완료되었거나 출고할 수 없는 수주 품목입니다.")

    waiting = _waiting_rows(db, item.part_no)
    if not waiting:
        raise HTTPException(409, "출고 가능한 출고대기LOT가 없습니다.")

    waiting_ids = [box.id for box, _ in waiting]
    selected_ids = list(dict.fromkeys(payload.selected_box_ids))
    reserved_ids = list(dict.fromkeys(payload.reserved_box_ids))
    if len(selected_ids) != len(payload.selected_box_ids) or len(reserved_ids) != len(payload.reserved_box_ids):
        raise HTTPException(409, "동일 LOT가 중복 배정되어 있습니다.")
    if set(selected_ids) & set(reserved_ids):
        raise HTTPException(409, "다른 수주 품목에 이미 배정된 LOT가 중복 포함되어 있습니다.")

    # 같은 출고전표에서 동일 품번의 다른 수주 품목에 잡힌 LOT도 FIFO 선점으로 본다.
    occupied_ids = reserved_ids + selected_ids
    if occupied_ids:
        expected_prefix = waiting_ids[:len(occupied_ids)]
        if set(occupied_ids) != set(expected_prefix):
            raise HTTPException(409, "현재 출고전표의 LOT 배정이 선입선출 기준과 일치하지 않습니다. 해당 품번 배정을 초기화해 주세요.")

    scanned = payload.lot_no.strip().lower()
    scanned_index = next(
        (
            index
            for index, (box, _) in enumerate(waiting)
            if str(box.package_lot_no or "").lower() == scanned
        ),
        None,
    )
    if scanned_index is None:
        raise HTTPException(404, "해당 품번의 출고 가능한 출고대기LOT를 찾을 수 없습니다.")

    reserved_set = set(reserved_ids)
    selected_set = set(selected_ids)
    start_index = len(occupied_ids)
    remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)

    # 현재 품목에 이미 배정된 LOT는 그대로 유지하고, 스캔 LOT까지를 상한으로 추가 자동배정한다.
    selected_rows = [(box, master) for box, master in waiting if box.id in selected_set]
    allocated_qty = sum(float(box.box_qty or 0) for box, _ in selected_rows)

    if scanned_index >= start_index:
        for index in range(start_index, scanned_index + 1):
            box, master = waiting[index]
            if box.id in reserved_set or box.id in selected_set:
                continue
            box_qty = float(box.box_qty or 0)
            if box_qty <= 0:
                continue
            if allocated_qty + box_qty > remaining_qty + 1e-9:
                break
            selected_rows.append((box, master))
            selected_set.add(box.id)
            allocated_qty += box_qty
            if allocated_qty >= remaining_qty - 1e-9:
                break

    if not selected_rows:
        raise HTTPException(409, "수주 잔량에 배정 가능한 완전 박스가 없습니다. 부분 박스 출고는 지원하지 않습니다.")

    # 실제 FIFO 순서대로 반환
    selected_rows.sort(key=lambda row: waiting_ids.index(row[0].id))
    allocations = [
        {
            "id": box.id,
            "package_lot_no": box.package_lot_no,
            "box_qty": float(box.box_qty or 0),
            "packing_date": master.packing_date,
            "part_no": master.part_no,
            "part_name": master.part_name,
            "fifo_order": waiting_ids.index(box.id) + 1,
        }
        for box, master in selected_rows
    ]
    allocated_qty = sum(row["box_qty"] for row in allocations)

    return {
        "scanned_lot_no": payload.lot_no.strip(),
        "allocations": allocations,
        "allocated_qty": allocated_qty,
        "remaining_qty": remaining_qty,
        "auto_added_count": max(len(allocations) - len(selected_ids), 0),
        "is_full_allocated": allocated_qty >= remaining_qty - 1e-9,
    }
