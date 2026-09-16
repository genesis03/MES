from fastapi import HTTPException
from sqlalchemy.orm import Session

from models.packing import PackingBox, PackingMaster
from models.sales import ShipmentDirectLot, ShipmentItem


def _shipping_prefix(shipping_date: str) -> str:
    date_text = (shipping_date or "").replace("-", "")
    if len(date_text) != 8 or not date_text.isdigit():
        raise HTTPException(422, "포장/출고 기준일자는 YYYY-MM-DD 형식이어야 합니다.")
    return f"{date_text[2:]}01"


def _lot_suffix(lot_no: str | None, prefix: str) -> int | None:
    if not lot_no:
        return None
    text = str(lot_no)
    if not text.startswith(prefix) or len(text) != len(prefix) + 3:
        return None
    suffix = text[-3:]
    return int(suffix) if suffix.isdigit() else None


def used_shipping_sequences(db: Session, part_no: str, shipping_date: str) -> set[int]:
    """동일 품번/동일 날짜에서 이미 사용된 포장(=출고) LOT 순번을 수집합니다.

    양산 포장 BOX와 샘플/개발 직출고 BOX가 같은 YYMMDD01xxx 번호 공간을 공유합니다.
    다른 품번은 동일 번호를 사용할 수 있습니다.
    """
    prefix = _shipping_prefix(shipping_date)
    used: set[int] = set()

    packing_rows = (
        db.query(PackingBox.package_lot_no)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(
            PackingMaster.part_no == part_no,
            PackingBox.package_lot_no.like(prefix + "%"),
        )
        .all()
    )
    for (lot_no,) in packing_rows:
        suffix = _lot_suffix(lot_no, prefix)
        if suffix is not None:
            used.add(suffix)

    direct_rows = (
        db.query(ShipmentDirectLot.outbound_lot_no)
        .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
        .filter(
            ShipmentItem.part_no == part_no,
            ShipmentDirectLot.outbound_lot_no.like(prefix + "%"),
        )
        .all()
    )
    for (lot_no,) in direct_rows:
        suffix = _lot_suffix(lot_no, prefix)
        if suffix is not None:
            used.add(suffix)

    return used


def next_shipping_lot_no(
    db: Session,
    part_no: str,
    shipping_date: str,
    reserved: set[str] | None = None,
) -> str:
    """포장 LOT = 출고 LOT 발번.

    형식: YYMMDD + 01 + 3자리 순번
    유일성 기준: 품번 + 포장/출고 LOT
    순번 공간: 동일 품번/동일 날짜에서 양산 포장과 샘플/개발 직출고가 공유
    """
    prefix = _shipping_prefix(shipping_date)
    used = used_shipping_sequences(db, part_no, shipping_date)
    for lot_no in reserved or set():
        suffix = _lot_suffix(lot_no, prefix)
        if suffix is not None:
            used.add(suffix)

    for seq in range(1, 1000):
        if seq not in used:
            return f"{prefix}{seq:03d}"

    raise HTTPException(409, f"{part_no} / {prefix}의 포장(출고) LOT 순번 001~999를 모두 사용했습니다.")
