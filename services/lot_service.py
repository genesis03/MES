from fastapi import HTTPException

from models.models import PurchaseInboundItem
from models.production_lot import ProductionLotModel


LOT_PREFIXES = {
    "INBOUND": "L",
    "COMPLEX_LATHE": "LX",
    "MACHINING": "LB",
    "TAPPING": "LB",
    "SERRATION": "LD",
    "SILVER_PLATING": "LZ",
    "OUTSOURCE_CNC": "LC",
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
    예: LX202609230201

    마지막 2자리 순번은 동일 작업일 + 동일 Prefix 전체 설비가 공유합니다.
    예:
      LX...0201 이후 LX...0101 재사용 금지 → 다음 LX는 ...02
      LX...0201과 LB...0101은 Prefix가 다르므로 허용
    """
    base = lot_base(prefix, work_date, equipment_no)
    date_text = (work_date or "").replace("-", "")
    date_prefix = f"{prefix}{date_text}"
    reserved = set(reserved or ())

    existing = {
        row[0]
        for row in db.query(ProductionLotModel.lot_no)
        .filter(ProductionLotModel.lot_no.like(date_prefix + "%"))
        .all()
        if row[0]
    }
    existing.update(
        row[0]
        for row in db.query(PurchaseInboundItem.internal_lot_no)
        .filter(PurchaseInboundItem.internal_lot_no.like(date_prefix + "%"))
        .all()
        if row[0]
    )
    existing.update(reserved)

    used = set()
    expected_length = len(date_prefix) + 4  # 설비번호 2자리 + 순번 2자리
    for lot_no in existing:
        if lot_no.startswith(date_prefix) and len(lot_no) == expected_length:
            suffix = lot_no[-2:]
            if suffix.isdigit():
                used.add(int(suffix))

    for sequence in range(1, 100):
        if sequence not in used:
            return f"{base}{sequence:02d}"

    raise HTTPException(
        409,
        f"{date_prefix}의 동일 공정 일일 LOT 순번 01~99를 모두 사용했습니다.",
    )
