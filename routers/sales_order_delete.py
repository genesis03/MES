from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.sales import SalesOrderItem, SalesOrderMaster, ShipmentItem, ShipmentMaster

router = APIRouter(tags=["Sales"])


@router.delete("/api/sales/orders/{order_id}")
def delete_sales_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.query(SalesOrderMaster).filter(SalesOrderMaster.id == order_id).first()
    if not order:
        raise HTTPException(404, "수주를 찾을 수 없습니다.")

    has_shipment_master = (
        db.query(ShipmentMaster.id)
        .filter(ShipmentMaster.sales_order_id == order.id)
        .first()
        is not None
    )
    has_shipment_item = (
        db.query(ShipmentItem.id)
        .join(SalesOrderItem, SalesOrderItem.id == ShipmentItem.sales_order_item_id)
        .filter(SalesOrderItem.order_id == order.id)
        .first()
        is not None
    )
    has_shipped_qty = any(float(item.shipped_qty or 0) > 1e-9 for item in order.items)

    if has_shipment_master or has_shipment_item or has_shipped_qty:
        raise HTTPException(409, "출고 이력이 있는 수주는 삭제할 수 없습니다.")

    order_no = order.order_no
    db.delete(order)
    db.commit()
    return {"message": f"수주 {order_no}가 삭제되었습니다.", "order_no": order_no}
