from core.database import SessionLocal
from models.equipment import EquipmentMaster
from models.models import ItemMasterModel
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_lot import ProductionLotModel
from services.lot_service import LOT_PREFIXES, next_lot_no


def _lot_prefix(performance: ProductionPerformance) -> str:
    if (performance.performance_type or "").upper() == "ASSEMBLY":
        return LOT_PREFIXES["ASSEMBLY"]
    if (performance.process_code or "").upper() == "LT":
        return LOT_PREFIXES["COMPLEX_LATHE"]
    return LOT_PREFIXES["MACHINING"]


def ensure_output_lot_for_performance(db, performance: ProductionPerformance) -> ProductionLotModel:
    marker = f"PERF:{performance.id}"
    existing = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.note.like(marker + "%"))
        .first()
    )
    if existing:
        return existing

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
        note=f"{marker}|생산실적 자동생성",
    )
    db.add(lot)
    db.flush()
    return lot


def ensure_production_output_lots() -> None:
    """기존 완료 생산실적 중 생산 LOT가 없는 건도 서버 시작 시 보강합니다."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ProductionPerformance)
            .filter(ProductionPerformance.good_qty > 0)
            .order_by(ProductionPerformance.id.asc())
            .all()
        )
        for performance in rows:
            ensure_output_lot_for_performance(db, performance)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
