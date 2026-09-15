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
    if len(selected_ids) != len(payload.selected_box_ids):
        raise HTTPException(409, "동일 LOT가 중복 배정되어 있습니다.")

    # 현재 배정은 항상 FIFO 앞쪽 연속 구간이어야 합니다.
    if selected_ids and selected_ids != waiting_ids[:len(selected_ids)]:
        raise HTTPException(409, "LOT 배정 순서가 선입선출 기준과 일치하지 않습니다. 배정을 초기화해 주세요.")

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

    # 이미 배정한 구간보다 앞 LOT를 다시 스캔한 경우 현재 배정을 그대로 유지합니다.
    if scanned_index < len(selected_ids):
        selected_rows = waiting[:len(selected_ids)]
    else:
        remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)
        selected_rows = waiting[:len(selected_ids)]
        allocated_qty = sum(float(box.box_qty or 0) for box, _ in selected_rows)

        # 사용자가 뒤 LOT를 스캔하면 그 LOT를 상한선으로 보고,
        # 실제 배정은 가장 오래된 LOT부터 수주 잔량 범위까지 자동 채웁니다.
        for index in range(len(selected_ids), scanned_index + 1):
            box, master = waiting[index]
            box_qty = float(box.box_qty or 0)
            if box_qty <= 0:
                continue
            if allocated_qty + box_qty > remaining_qty + 1e-9:
                break
            selected_rows.append((box, master))
            allocated_qty += box_qty
            if allocated_qty >= remaining_qty - 1e-9:
                break

    if not selected_rows:
        raise HTTPException(409, "수주 잔량에 배정 가능한 완전 박스가 없습니다. 부분 박스 출고는 지원하지 않습니다.")

    allocations = [
        {
            "id": box.id,
            "package_lot_no": box.package_lot_no,
            "box_qty": float(box.box_qty or 0),
            "packing_date": master.packing_date,
            "part_no": master.part_no,
            "part_name": master.part_name,
            "fifo_order": index + 1,
        }
        for index, (box, master) in enumerate(selected_rows)
    ]
    allocated_qty = sum(row["box_qty"] for row in allocations)
    remaining_qty = max(float(item.order_qty or 0) - float(item.shipped_qty or 0), 0.0)

    return {
        "scanned_lot_no": payload.lot_no.strip(),
        "allocations": allocations,
        "allocated_qty": allocated_qty,
        "remaining_qty": remaining_qty,
        "auto_added_count": max(len(allocations) - len(selected_ids), 0),
        "is_full_allocated": allocated_qty >= remaining_qty - 1e-9,
    }
