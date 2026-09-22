from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.models import ItemMasterModel
from models.packing import PackingBox, PackingMaster
from models.sales import ShipmentDirectLot, ShipmentItem
from models.shipping_lot import ShippingLotRegistry


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
    """동일 품목(item_id)/동일 날짜에서 이미 사용된 포장(=출고) LOT 순번을 수집합니다."""
    prefix = _shipping_prefix(shipping_date)
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no).first()
    if item is None:
        raise HTTPException(404, f"품목을 찾을 수 없습니다. ({part_no})")
    used: set[int] = {
        int(row[0])
        for row in db.query(ShippingLotRegistry.sequence)
        .filter(
            ShippingLotRegistry.item_id == item.id,
            ShippingLotRegistry.lot_date == shipping_date,
        )
        .all()
    }

    # 레지스트리 도입 전 데이터가 누락되어 있어도 기존 원장에서 다시 방어합니다.
    packing_rows = (
        db.query(PackingBox.package_lot_no)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(
            PackingMaster.item_id == item.id,
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
            ShipmentItem.item_id == item.id,
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
    source_type: str | None = None,
) -> str:
    """포장 LOT = 출고 LOT 발번 및 예약.

    형식: YYMMDD + 01 + 3자리 순번
    유일성 기준: 품번 + 포장/출고 LOT
    순번 공간: 동일 품번/동일 날짜에서 양산 포장과 샘플/개발 직출고가 공유

    발번 즉시 공용 레지스트리에 예약하고 flush하여 동시 요청에서도 같은 품번/LOT 중복을 DB가 차단합니다.
    """
    prefix = _shipping_prefix(shipping_date)
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no).first()
    if item is None:
        raise HTTPException(404, f"품목을 찾을 수 없습니다. ({part_no})")
    used = used_shipping_sequences(db, item.part_no, shipping_date)
    for lot_no in reserved or set():
        suffix = _lot_suffix(lot_no, prefix)
        if suffix is not None:
            used.add(suffix)

    for seq in range(1, 1000):
        if seq in used:
            continue
        lot_no = f"{prefix}{seq:03d}"
        registry = ShippingLotRegistry(
            item_id=item.id,
            part_no=item.part_no,
            lot_no=lot_no,
            lot_date=shipping_date,
            sequence=seq,
            source_type=(source_type or "").strip() or None,
        )
        try:
            with db.begin_nested():
                db.add(registry)
                db.flush()
            return lot_no
        except IntegrityError:
            # 다른 동시 요청이 같은 번호를 먼저 예약했으면 다음 번호를 시도합니다.
            used.add(seq)
            continue

    raise HTTPException(409, f"{part_no} / {prefix}의 포장(출고) LOT 순번 001~999를 모두 사용했습니다.")
