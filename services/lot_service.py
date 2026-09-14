from fastapi import HTTPException

from models.models import PurchaseInboundItem
from models.production_lot import ProductionLotModel


LOT_PREFIXES = {
    "INBOUND": "L",
    "COMPLEX_LATHE": "LX",
    "MACHINING": "LB",
    "SILVER_PLATING": "LZ",
    "ASSEMBLY": "LA",
}


def normalize_equipment_no(equipment_no: int | str | None) -> str:
    if equipment_no in (None, ""):
        return "01"
    try:
        value = int(equipment_no)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "설비번호는 01~99 범위의 숫자여야 합니다.") from exc
    if value < 1 or value > 99:
        raise HTTPException(422, "설비번호는 01~99 범위여야 합니다.")
    return f"{value:02d}"


def lot_base(prefix: str, work_date: str, equipment_no: int | str | None = None) -> str:
    if prefix not in LOT_PREFIXES.values():
        raise HTTPException(422, f"지원하지 않는 LOT 공정 Prefix입니다: {prefix}")
    date_text = (work_date or "").replace("-", "")
    if len(date_text) != 8 or not date_text.isdigit():
        raise HTTPException(422, "LOT 기준일자는 YYYY-MM-DD 형식이어야 합니다.")
    return f"{prefix}{date_text}{normalize_equipment_no(equipment_no)}"


def next_lot_no(db, prefix: str, work_date: str, equipment_no: int | str | None = None, reserved=None) -> str:
    """회사 표준 LOT 번호를 발번합니다.

    형식: 공정Prefix + YYYYMMDD + 설비번호 2자리 + 일일순번 2자리
    예: LZ202609140101
    순번은 동일 일자/공정/설비 기준 01~99입니다.
    """
    base = lot_base(prefix, work_date, equipment_no)
    reserved = set(reserved or ())

    existing = {
        row[0]
        for row in db.query(ProductionLotModel.lot_no)
        .filter(ProductionLotModel.lot_no.like(base + "%"))
        .all()
        if row[0]
    }
    existing.update(
        row[0]
        for row in db.query(PurchaseInboundItem.internal_lot_no)
        .filter(PurchaseInboundItem.internal_lot_no.like(base + "%"))
        .all()
        if row[0]
    )
    existing.update(reserved)

    used = set()
    for lot_no in existing:
        if lot_no.startswith(base) and len(lot_no) == len(base) + 2:
            suffix = lot_no[-2:]
            if suffix.isdigit():
                used.add(int(suffix))

    for sequence in range(1, 100):
        if sequence not in used:
            return f"{base}{sequence:02d}"

    raise HTTPException(409, f"{base}의 일일 LOT 순번 01~99를 모두 사용했습니다.")
