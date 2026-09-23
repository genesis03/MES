from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.models import ItemMasterModel, ShippingMasterModel
from models.sales import SalesOrderMaster, ShipmentItem, ShipmentMaster
from services.sales_order_service import sync_order_status

router = APIRouter(tags=["Shipping Inquiry"])
templates = Jinja2Templates(directory="templates")


class ShipmentUpdateInput(BaseModel):
    shipment_date: str = Field(min_length=10, max_length=10)
    note: str | None = Field(default=None, max_length=1000)


def _serialize_shipment(db: Session, row: ShipmentMaster, detail: bool = False):
    items = []
    total_qty = 0.0
    total_boxes = 0
    part_nos = []
    order_ids = []
    order_nos = []
    has_direct = False

    for item in row.items:
        master_item = db.get(ItemMasterModel, item.item_id) if item.item_id else None
        current_part_no = master_item.part_no if master_item else item.part_no
        qty = float(item.shipped_qty or 0)
        boxes = list(item.boxes)
        direct_lots = list(getattr(item, "direct_lots", []) or [])
        direct_outbound_lots = []
        for direct in direct_lots:
            outbound_lot_no = str(getattr(direct, "outbound_lot_no", None) or "").strip()
            if outbound_lot_no and outbound_lot_no not in direct_outbound_lots:
                direct_outbound_lots.append(outbound_lot_no)
        if direct_lots:
            has_direct = True
        total_qty += qty
        # BOX 수는 실제 포장 LOT(=출고 LOT) 수를 의미합니다. 샘플/개발도 BOX별 LOT를 셉니다.
        total_boxes += len(boxes) if boxes else (len(direct_outbound_lots) or (1 if direct_lots else 0))
        if current_part_no and current_part_no not in part_nos:
            part_nos.append(current_part_no)

        sales_item = item.sales_order_item
        sales_order = sales_item.order if sales_item else None
        if sales_order:
            if sales_order.id not in order_ids:
                order_ids.append(sales_order.id)
            if sales_order.order_no not in order_nos:
                order_nos.append(sales_order.order_no)

        item_data = {
            "id": item.id,
            "item_id": item.item_id,
            "part_no": current_part_no,
            "part_name": sales_item.part_name if sales_item else "",
            "shipped_qty": qty,
            "unit": item.unit or "EA",
            "order_qty": float(sales_item.order_qty or 0) if sales_item else 0.0,
            "sales_order_item_id": item.sales_order_item_id,
            "sales_order_id": sales_order.id if sales_order else None,
            "order_no": sales_order.order_no if sales_order else "",
            "order_status": sales_order.status if sales_order else "",
            "order_type": (getattr(sales_order, "order_type", None) or "NORMAL") if sales_order else "",
            "transaction_type": (getattr(sales_order, "transaction_type", None) or "PAID") if sales_order else "",
            "delivery_due_date": sales_order.delivery_due_date if sales_order else "",
            "manager_name": sales_order.manager_name if sales_order else "",
            "box_count": len(boxes),
            "direct_lot_count": len(direct_outbound_lots) or (1 if direct_lots else 0),
            "shipment_mode": "DIRECT" if direct_lots else "PACKED",
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
            item_data["direct_lots"] = [
                {
                    "id": direct.id,
                    "production_lot_id": direct.production_lot_id,
                    "box_no": getattr(direct, "box_no", None),
                    "outbound_lot_no": getattr(direct, "outbound_lot_no", None) or "",
                    "lot_no": direct.source_lot_no,
                    "source_lot_no": direct.source_lot_no,
                    "source_item_id": direct.source_item_id,
                    "source_part_no": (
                        db.get(ItemMasterModel, direct.source_item_id).part_no
                        if direct.source_item_id and db.get(ItemMasterModel, direct.source_item_id)
                        else direct.source_part_no
                    ),
                    "shipped_qty": float(direct.shipped_qty or 0),
                }
                for direct in direct_lots
            ]
        items.append(item_data)

    order_no = ", ".join(order_nos)
    part_summary = ""
    if part_nos:
        part_summary = part_nos[0] if len(part_nos) == 1 else f"{part_nos[0]} 외 {len(part_nos) - 1}건"

    return {
        "id": row.id,
        "shipment_no": row.shipment_no,
        "shipment_date": row.shipment_date,
        "sales_order_id": row.sales_order_id,
        "sales_order_ids": order_ids,
        "order_no": order_no,
        "order_nos": order_nos,
        "order_count": len(order_nos),
        "customer_id": row.customer_id,
        "customer_name": row.customer_name,
        "status": row.status,
        "shipment_mode": "DIRECT" if has_direct else "PACKED",
        "fifo_exception": (getattr(row, "fifo_exception", None) or "N") == "Y",
        "fifo_exception_reason": getattr(row, "fifo_exception_reason", None) or "",
        "note": row.note or "",
        "created_by": row.created_by or "",
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
        "part_nos": part_nos,
        "part_summary": part_summary,
        "total_qty": total_qty,
        "total_boxes": total_boxes,
        "items": items,
    }


@router.get("/shipping/inquiry", response_class=HTMLResponse)
def shipping_inquiry_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="shipping_inquiry.html", context={"request": request, "user": current_user})


@router.get("/api/shipping/inquiry")
def shipping_inquiry(
    start_date: str | None = Query(None, max_length=10),
    end_date: str | None = Query(None, max_length=10),
    customer_id: int | None = Query(None, gt=0),
    customer_name: str | None = Query(None, max_length=100),
    item_id: list[int] | None = Query(None),
    shipment_no: str | None = Query(None, max_length=30),
    limit: int = Query(1000, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ShipmentMaster)
    if start_date:
        query = query.filter(ShipmentMaster.shipment_date >= start_date)
    if end_date:
        query = query.filter(ShipmentMaster.shipment_date <= end_date)
    if customer_id:
        query = query.filter(ShipmentMaster.customer_id == customer_id)
    elif customer_name and customer_name.strip():
        # 구버전 호출 호환용. 화면은 판매처 master id를 사용합니다.
        query = query.filter(ShipmentMaster.customer_name.contains(customer_name.strip(), autoescape=True))
    if shipment_no and shipment_no.strip():
        query = query.filter(ShipmentMaster.shipment_no.contains(shipment_no.strip(), autoescape=True))

    selected_item_ids = sorted({value for value in (item_id or []) if value > 0})
    if selected_item_ids:
        query = query.filter(
            ShipmentMaster.items.any(ShipmentItem.item_id.in_(selected_item_ids))
        )

    rows = (
        query.order_by(ShipmentMaster.shipment_date.desc(), ShipmentMaster.id.desc())
        .limit(limit)
        .all()
    )
    result = [_serialize_shipment(db, row) for row in rows]
    return {"items": result, "total": len(result)}


@router.get("/api/shipping/inquiry/{shipment_id}")
def shipping_inquiry_detail(shipment_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    row = db.get(ShipmentMaster, shipment_id)
    if not row:
        raise HTTPException(404, "출고 내역을 찾을 수 없습니다.")
    return _serialize_shipment(db, row, detail=True)


@router.put("/api/shipping/inquiry/{shipment_id}")
def update_shipment(
    shipment_id: int,
    payload: ShipmentUpdateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    shipment = db.get(ShipmentMaster, shipment_id)
    if not shipment:
        raise HTTPException(404, "출고 내역을 찾을 수 없습니다.")

    has_direct = any(
        bool(getattr(item, "direct_lots", []) or [])
        for item in shipment.items
    )
    if has_direct and payload.shipment_date != shipment.shipment_date:
        raise HTTPException(
            409,
            "샘플/개발 직출고는 출고 LOT 번호에 출고일자가 포함되어 있어 출고일자를 변경할 수 없습니다.",
        )

    # 이미 출하 인쇄용 작업 데이터가 생성된 경우 날짜/비고 수정 후 재생성되도록
    # 해당 출고의 포장 LOT가 포함된 staging 데이터만 제거합니다.
    lot_nos = {
        str(box.package_lot_no).strip()
        for item in shipment.items
        for box in item.boxes
        if box.package_lot_no
    }
    staging_removed = 0
    if lot_nos:
        for row in db.query(ShippingMasterModel).all():
            raw = str(row.row_json or "")
            if any(lot_no in raw for lot_no in lot_nos):
                db.delete(row)
                staging_removed += 1

    shipment.shipment_date = payload.shipment_date
    shipment.note = (payload.note or "").strip() or None
    db.commit()

    return {
        "status": "success",
        "shipment_id": shipment.id,
        "shipment_no": shipment.shipment_no,
        "staging_removed": staging_removed,
        "message": (
            "출고 내역을 수정했습니다."
            + (f" 기존 인쇄 작업 데이터 {staging_removed}건은 재생성하도록 정리했습니다." if staging_removed else "")
        ),
    }


@router.delete("/api/shipping/inquiry/{shipment_id}")
def delete_shipment(shipment_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    shipment = db.get(ShipmentMaster, shipment_id)
    if not shipment:
        raise HTTPException(404, "출고 내역을 찾을 수 없습니다.")

    waiting_lots = {
        str(box.package_lot_no).strip()
        for item in shipment.items
        for box in item.boxes
        if box.package_lot_no
    }
    staging_removed = 0
    if waiting_lots:
        staging_rows = db.query(ShippingMasterModel).all()
        for staging_row in staging_rows:
            raw = str(staging_row.row_json or "")
            if any(lot_no in raw for lot_no in waiting_lots):
                db.delete(staging_row)
                staging_removed += 1

    # 샘플/개발 직출고는 생산 LOT -> BOX별 포장 LOT(=출고 LOT) 계보로 기록합니다.
    # 과거 직출고(SHIP:출고번호:...) 형식도 함께 정리해 이전 데이터 삭제 호환성을 유지합니다.
    outbound_lots = {
        str(getattr(direct, "outbound_lot_no", None) or "").strip()
        for item in shipment.items
        for direct in getattr(item, "direct_lots", [])
        if str(getattr(direct, "outbound_lot_no", None) or "").strip()
    }
    direct_relations = (
        db.query(LotRelationModel)
        .filter(LotRelationModel.child_lot_no.like(f"SHIP:{shipment.shipment_no}:%"))
        .all()
    )
    if outbound_lots:
        direct_relations.extend(
            db.query(LotRelationModel)
            .filter(
                LotRelationModel.process_code == "SHIP_DIRECT",
                LotRelationModel.child_lot_no.in_(outbound_lots),
            )
            .all()
        )
    unique_relations = {relation.id: relation for relation in direct_relations}
    direct_relation_removed = len(unique_relations)
    for relation in unique_relations.values():
        db.delete(relation)

    affected_orders = {}
    for item in shipment.items:
        sales_item = item.sales_order_item
        if not sales_item:
            continue
        sales_item.shipped_qty = max(float(sales_item.shipped_qty or 0) - float(item.shipped_qty or 0), 0.0)
        if sales_item.order:
            affected_orders[sales_item.order.id] = sales_item.order

    for order in affected_orders.values():
        sync_order_status(order)

    shipment_no = shipment.shipment_no
    db.delete(shipment)
    db.commit()

    texts = []
    if staging_removed:
        texts.append(f"LOT 계산/라벨 작업 데이터 {staging_removed}건")
    if direct_relation_removed:
        texts.append(f"생산 LOT 직출고 사용이력 {direct_relation_removed}건")
    sync_text = f" {' / '.join(texts)}도 함께 정리했습니다." if texts else ""
    return {
        "status": "success",
        "shipment_no": shipment_no,
        "staging_removed": staging_removed,
        "direct_relation_removed": direct_relation_removed,
        "message": f"출고 내역을 삭제했습니다. 연결된 수주의 출고수량과 LOT 사용상태가 복원되었습니다.{sync_text}",
    }