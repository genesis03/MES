from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ShippingMasterModel
from models.sales import SalesOrderMaster, ShipmentMaster

router = APIRouter(tags=["Shipping Inquiry"])
templates = Jinja2Templates(directory="templates")


def _sync_order_status(order: SalesOrderMaster):
    if not order.items:
        order.status = "ORDERED"
        return

    for item in order.items:
        shipped = float(item.shipped_qty or 0)
        ordered = float(item.order_qty or 0)
        if shipped >= ordered - 1e-9:
            item.status = "COMPLETED"
        elif shipped > 1e-9:
            item.status = "PARTIAL"
        else:
            item.status = "WAITING"

    if all(float(x.shipped_qty or 0) >= float(x.order_qty or 0) - 1e-9 for x in order.items):
        order.status = "COMPLETED"
    elif any(float(x.shipped_qty or 0) > 1e-9 for x in order.items):
        order.status = "PARTIAL"
    else:
        order.status = "ORDERED"


def _serialize_shipment(row: ShipmentMaster, detail: bool = False):
    items = []
    total_qty = 0.0
    total_boxes = 0
    part_nos = []

    for item in row.items:
        qty = float(item.shipped_qty or 0)
        boxes = list(item.boxes)
        total_qty += qty
        total_boxes += len(boxes)
        if item.part_no and item.part_no not in part_nos:
            part_nos.append(item.part_no)

        sales_item = item.sales_order_item
        item_data = {
            "id": item.id,
            "part_no": item.part_no,
            "part_name": sales_item.part_name if sales_item else "",
            "shipped_qty": qty,
            "unit": item.unit or "EA",
            "order_qty": float(sales_item.order_qty or 0) if sales_item else 0.0,
            "sales_order_item_id": item.sales_order_item_id,
            "box_count": len(boxes),
        }
        if detail:
            item_data["boxes"] = [
                {
                    "id": box.id,
                    "packing_box_id": box.packing_box_id,
                    "waiting_lot_no": box.package_lot_no,
                    "shipped_qty": float(box.shipped_qty or 0),
                }
                for box in boxes
            ]
        items.append(item_data)

    order_no = ""
    if row.items and row.items[0].sales_order_item and row.items[0].sales_order_item.order:
        order_no = row.items[0].sales_order_item.order.order_no

    return {
        "id": row.id,
        "shipment_no": row.shipment_no,
        "shipment_date": row.shipment_date,
        "sales_order_id": row.sales_order_id,
        "order_no": order_no,
        "customer_id": row.customer_id,
        "customer_name": row.customer_name,
        "status": row.status,
        "note": row.note or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        "part_nos": part_nos,
        "total_qty": total_qty,
        "total_boxes": total_boxes,
        "items": items,
    }


@router.get("/shipping/inquiry", response_class=HTMLResponse)
def shipping_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="shipping_inquiry.html",
        context={"request": request, "user": current_user},
    )


@router.get("/api/shipping/inquiry")
def shipping_inquiry(
    start_date: str | None = Query(None, max_length=10),
    end_date: str | None = Query(None, max_length=10),
    customer_name: str | None = Query(None, max_length=100),
    part_no: str | None = Query(None, max_length=50),
    shipment_no: str | None = Query(None, max_length=30),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ShipmentMaster)
    if start_date:
        query = query.filter(ShipmentMaster.shipment_date >= start_date)
    if end_date:
        query = query.filter(ShipmentMaster.shipment_date <= end_date)
    if customer_name and customer_name.strip():
        query = query.filter(ShipmentMaster.customer_name.contains(customer_name.strip(), autoescape=True))
    if shipment_no and shipment_no.strip():
        query = query.filter(ShipmentMaster.shipment_no.contains(shipment_no.strip(), autoescape=True))

    rows = query.order_by(ShipmentMaster.shipment_date.desc(), ShipmentMaster.id.desc()).limit(1000).all()
    result = []
    keyword = (part_no or "").strip().lower()
    for row in rows:
        data = _serialize_shipment(row)
        if keyword and not any(keyword in str(value or "").lower() for value in data["part_nos"]):
            continue
        result.append(data)
    return {"items": result, "total": len(result)}


@router.get("/api/shipping/inquiry/{shipment_id}")
def shipping_inquiry_detail(
    shipment_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    row = db.get(ShipmentMaster, shipment_id)
    if not row:
        raise HTTPException(404, "출고 내역을 찾을 수 없습니다.")
    return _serialize_shipment(row, detail=True)


@router.delete("/api/shipping/inquiry/{shipment_id}")
def delete_shipment(
    shipment_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    shipment = db.get(ShipmentMaster, shipment_id)
    if not shipment:
        raise HTTPException(404, "출고 내역을 찾을 수 없습니다.")

    waiting_lots = [
        box.package_lot_no
        for item in shipment.items
        for box in item.boxes
        if box.package_lot_no
    ]
    for lot_no in waiting_lots:
        used = (
            db.query(ShippingMasterModel.id)
            .filter(ShippingMasterModel.row_json.contains(lot_no, autoescape=True))
            .first()
        )
        if used:
            raise HTTPException(
                409,
                f"출고 내역 및 LOT 계산/라벨 데이터에서 이미 사용된 출고대기LOT가 있어 삭제할 수 없습니다: {lot_no}",
            )

    affected_orders = {}
    for item in shipment.items:
        sales_item = item.sales_order_item
        if not sales_item:
            continue
        sales_item.shipped_qty = max(
            float(sales_item.shipped_qty or 0) - float(item.shipped_qty or 0),
            0.0,
        )
        if sales_item.order:
            affected_orders[sales_item.order.id] = sales_item.order

    for order in affected_orders.values():
        _sync_order_status(order)

    shipment_no = shipment.shipment_no
    db.delete(shipment)
    db.commit()

    return {
        "status": "success",
        "shipment_no": shipment_no,
        "message": "출고 내역을 삭제했습니다. 수주 출고수량과 출고대기LOT 사용상태가 복원되었습니다.",
    }
