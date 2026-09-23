from models.sales import SalesOrderMaster


def sync_order_status(order: SalesOrderMaster) -> None:
    """수주 품목의 출고수량을 기준으로 품목/수주 상태를 일관되게 동기화합니다."""
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

    if all(float(item.shipped_qty or 0) >= float(item.order_qty or 0) - 1e-9 for item in order.items):
        order.status = "COMPLETED"
    elif any(float(item.shipped_qty or 0) > 1e-9 for item in order.items):
        order.status = "PARTIAL"
    else:
        order.status = "ORDERED"
