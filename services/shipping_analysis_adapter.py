from datetime import datetime

from sqlalchemy.orm import Session

from models.sales import ShipmentMaster

# 현재 회사 고정값. 향후 회사정보 설정으로 이관하기 쉽도록 한 곳에서만 관리합니다.
COMPANY_NAME = "(주)유니코어텍"
COMPANY_UNIQUE_NO = "110657"

LEGACY_COLUMNS = [
    "textBox177",
    "textBox310",
    "barcode1",
    "textBox2",
    "textBox1",
    "textBox3",
    "textBox4",
    "textBox5",
    "textBox6",
    "textBox7",
    "textBox8",
    "textBox9",
    "textBox10",
    "textBox11",
    "textBox12",
]


def _qty_text(value: float) -> str:
    qty = float(value or 0)
    if qty.is_integer():
        return str(int(qty))
    return (f"{qty:.6f}").rstrip("0").rstrip(".")


def _delivery_date_text(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%y/%m/%d")
    except ValueError:
        return raw


def build_shipping_analysis_rows(db: Session, shipment_ids: list[int]) -> list[dict]:
    ids = list(dict.fromkeys(int(x) for x in shipment_ids if int(x) > 0))
    if not ids:
        return []

    shipments = (
        db.query(ShipmentMaster)
        .filter(ShipmentMaster.id.in_(ids))
        .order_by(ShipmentMaster.shipment_date.asc(), ShipmentMaster.id.asc())
        .all()
    )
    row_map = {row.id: row for row in shipments}
    ordered_shipments = [row_map[row_id] for row_id in ids if row_id in row_map]

    result: list[dict] = []
    for shipment in ordered_shipments:
        delivery_date = _delivery_date_text(shipment.shipment_date)
        for shipment_item in shipment.items:
            sales_item = shipment_item.sales_order_item
            part_name = sales_item.part_name if sales_item else ""
            part_no = shipment_item.part_no

            for box in shipment_item.boxes:
                qty_text = _qty_text(box.shipped_qty)
                waiting_lot = str(box.package_lot_no or "").strip()
                serial = f"{waiting_lot}{COMPANY_UNIQUE_NO}"
                barcode = f"P{part_no}Q{qty_text}S{serial}"

                result.append({
                    "textBox177": COMPANY_NAME,
                    "textBox310": "품 번",
                    "barcode1": barcode,
                    "textBox2": shipment.customer_name or "",
                    "textBox1": "품 명",
                    "textBox3": "수 량",
                    "textBox4": "SERIAL",
                    "textBox5": "납품일자",
                    "textBox6": delivery_date,
                    "textBox7": "출하검사",
                    "textBox8": "합      격",
                    "textBox9": part_no,
                    "textBox10": part_name or "",
                    "textBox11": qty_text,
                    "textBox12": serial,
                })

    return result
