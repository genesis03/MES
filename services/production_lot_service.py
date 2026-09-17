import re

from core.database import SessionLocal
from models.equipment import EquipmentMaster
from models.models import ItemMasterModel
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_lot import ProductionLotModel
from services.lot_service import LOT_PREFIXES, next_lot_no


_PERF_NOTE_RE = re.compile(r"^PERF:(\d+)(?:\||$)")


def performance_id_from_lot_note(note: str | None) -> int | None:
    match = _PERF_NOTE_RE.match(str(note or "").strip())
    return int(match.group(1)) if match else None


def output_lots_for_performance(db, performance_id: int):
    marker = f"PERF:{performance_id}"
    candidates = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.note.like(marker + "%"))
        .order_by(ProductionLotModel.id.asc())
        .all()
    )
    return [row for row in candidates if performance_id_from_lot_note(row.note) == performance_id]


def _lot_prefix(performance: ProductionPerformance) -> str:
    if (performance.performance_type or "").upper() == "ASSEMBLY":
        return LOT_PREFIXES["ASSEMBLY"]
    if (performance.process_code or "").upper() == "LT":
        return LOT_PREFIXES["COMPLEX_LATHE"]
    return LOT_PREFIXES["MACHINING"]


def ensure_output_lot_for_performance(db, performance: ProductionPerformance) -> ProductionLotModel:
    existing_rows = output_lots_for_performance(db, performance.id)
    if existing_rows:
        return existing_rows[0]

    order = db.get(ProductionWorkOrder, performance.work_order_id)
    if not order:
        raise RuntimeError(f"생산실적 {performance.id}의 작업지시를 찾을 수 없습니다.")

    equipment = db.get(EquipmentMaster, performance.equipment_id) if performance.equipment_id else None
    machine_no = equipment.machine_no if equipment else 1
    item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == order.part_no).first()

    lot_no = next_lot_no(
        db,
        _lot_prefix(performance),
        performance.performance_date,
        machine_no,
    )
    lot = ProductionLotModel(
        lot_no=lot_no,
        part_no=order.part_no,
        lot_qty=float(performance.good_qty or 0),
        storage_location=(item.inbound_loc if item else None),
        status="ACTIVE",
        note=f"PERF:{performance.id}|생산실적 자동생성",
    )
    db.add(lot)
    db.flush()
    return lot


def ensure_production_output_lots() -> None:
    """생산실적 LOT 보강 + 삭제된 실적의 고아 생산 LOT 비활성화."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ProductionPerformance)
            .filter(ProductionPerformance.good_qty > 0)
            .order_by(ProductionPerformance.id.asc())
            .all()
        )
        active_performance_ids = {row.id for row in rows}
        for performance in rows:
            ensure_output_lot_for_performance(db, performance)

        # 생산실적이 삭제됐는데 과거 버그로 생산 LOT만 남은 경우에는 재고로 다시 쓰지 못하게 한다.
        # 데이터 추적을 위해 행 자체를 자동 삭제하지 않고 ORPHANED 상태로만 전환한다.
        production_lots = (
            db.query(ProductionLotModel)
            .filter(
                ProductionLotModel.status == "ACTIVE",
                ProductionLotModel.note.like("PERF:%"),
            )
            .all()
        )
        for lot in production_lots:
            performance_id = performance_id_from_lot_note(lot.note)
            if performance_id is not None and performance_id not in active_performance_ids:
                lot.status = "ORPHANED"

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
