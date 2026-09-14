from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.database import SessionLocal
from models.models import ItemMasterModel
from models.production_lot import ProductionLotModel


PART_NO = "310188-A"
LOTS = [f"LX20260901{seq:03d}" for seq in range(1, 13)]
QTY = 100.0


def main():
    db = SessionLocal()
    try:
        part = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == PART_NO).first()
        if part is None:
            raise SystemExit(f"품목 마스터에 {PART_NO}가 없습니다. 먼저 품목을 등록하세요.")

        created = 0
        skipped = 0
        for lot_no in LOTS:
            exists = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot_no).first()
            if exists:
                skipped += 1
                continue
            db.add(ProductionLotModel(
                lot_no=lot_no,
                part_no=PART_NO,
                lot_qty=QTY,
                status="ACTIVE",
                note="외주가공 발주 기능 테스트용 LOT",
            ))
            created += 1
        db.commit()
        print(f"완료: 생성 {created}건 / 기존 {skipped}건 / 총 LOT {len(LOTS)}건")
        print(f"품번 {PART_NO}, LOT당 {QTY:g} EA, 총 {len(LOTS) * QTY:g} EA")
    finally:
        db.close()


if __name__ == "__main__":
    main()
