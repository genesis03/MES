from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import ItemMasterModel, ProcessModel
from models.packing import PackingLotAllocation, PackingMaster
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_outbound import SubcontractOutboundItem, SubcontractOutboundLot, SubcontractOutboundMaster
from services.production_lot_service import output_lots_for_performance

router = APIRouter(prefix="/api/production", tags=["Production Extra"])


def _is_super_admin(user) -> bool:
    if isinstance(user, dict):
        username = str(user.get("username") or "").strip().lower()
        role = str(user.get("role") or "").strip().upper()
    else:
        username = str(getattr(user, "username", "")).strip().lower()
        role = str(getattr(user, "role", "")).strip().upper()
    return username == "admin" or role == "SUPERADMIN"


def _performance_output_lots(db: Session, performance_id: int):
    return output_lots_for_performance(db, performance_id)


def _downstream_used_lots(db: Session, lot_nos: list[str]) -> list[str]:
    if not lot_nos:
        return []

    target_lots = {str(value).strip() for value in lot_nos if str(value or "").strip()}
    used = set()

    used.update(
        row[0]
        for row in db.query(LotConsumptionModel.lot_no)
        .filter(LotConsumptionModel.lot_no.in_(target_lots))
        .distinct()
        .all()
    )
    used.update(
        row[0]
        for row in db.query(LotRelationModel.parent_lot_no)
        .filter(LotRelationModel.parent_lot_no.in_(target_lots))
        .distinct()
        .all()
    )
    used.update(
        row[0]
        for row in (
            db.query(ProductionRunLotAllocation.lot_no)
            .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
            .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
            .filter(
                ProductionRunLotAllocation.lot_no.in_(target_lots),
                ProductionRun.status.in_(["IN_PROGRESS", "COMPLETED"]),
            )
            .distinct()
            .all()
        )
    )

    # 현재 가동 완료 로직은 LotConsumption을 기록하지만, 이전 실적/마이그레이션 데이터 중
    # source_lot_no만 남아 있는 경우도 있으므로 생산실적 원본 LOT까지 이중 확인한다.
    performance_sources = (
        db.query(ProductionPerformance.source_lot_no)
        .filter(
            ProductionPerformance.source_lot_no.isnot(None),
            ProductionPerformance.source_lot_no != "",
        )
        .all()
    )
    for (source_lot_no,) in performance_sources:
        for value in str(source_lot_no or "").split(","):
            lot_no = value.strip()
            if lot_no in target_lots:
                used.add(lot_no)

    # 외주 발주 단계에서 배정된 LOT도 사용 이력으로 본다.
    used.update(
        row[0]
        for row in (
            db.query(SubcontractLotAllocation.lot_no)
            .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
            .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
            .filter(
                SubcontractLotAllocation.lot_no.in_(target_lots),
                SubcontractOrderMaster.status != "CANCELLED",
            )
            .distinct()
            .all()
        )
    )

    # 실제 외주 출고가 완료되면 출고 당시 LOT 스냅샷은 subcontract_outbound_lots에 남는다.
    # 발주 상태/배정 데이터가 이후 변경되더라도 OUTBOUND 상태의 출고 이력이 있으면 원 생산실적 삭제를 막는다.
    used.update(
        row[0]
        for row in (
            db.query(SubcontractOutboundLot.lot_no)
            .join(SubcontractOutboundItem, SubcontractOutboundItem.id == SubcontractOutboundLot.outbound_item_id)
            .join(SubcontractOutboundMaster, SubcontractOutboundMaster.id == SubcontractOutboundItem.outbound_id)
            .filter(
                SubcontractOutboundLot.lot_no.in_(target_lots),
                SubcontractOutboundMaster.status == "OUTBOUND",
            )
            .distinct()
            .all()
        )
    )

    used.update(
        row[0]
        for row in (
            db.query(PackingLotAllocation.source_lot_no)
            .join(PackingMaster, PackingMaster.id == PackingLotAllocation.packing_id)
            .filter(
                PackingLotAllocation.source_lot_no.in_(target_lots),
                PackingMaster.status == "PACKED",
            )
            .distinct()
            .all()
        )
    )
    return sorted(value for value in used if value)


def _assert_performance_deletable(db: Session, performance: ProductionPerformance):
    output_lots = _performance_output_lots(db, performance.id)
    output_lot_nos = [row.lot_no for row in output_lots]
    used_lots = _downstream_used_lots(db, output_lot_nos)
    if used_lots:
        raise HTTPException(409, "다음 공정/외주/포장에서 이미 사용된 생산 LOT가 있어 실적을 삭제할 수 없습니다: " + ", ".join(used_lots))
    return output_lots


def _delete_performance(db: Session, performance: ProductionPerformance, output_lots=None):
    output_lots = output_lots if output_lots is not None else _assert_performance_deletable(db, performance)
    output_lot_nos = [row.lot_no for row in output_lots]
    order = db.get(ProductionWorkOrder, performance.work_order_id)
    db.query(LotConsumptionModel).filter(LotConsumptionModel.performance_id == performance.id).delete(synchronize_session=False)
    if output_lot_nos:
        db.query(LotRelationModel).filter(LotRelationModel.child_lot_no.in_(output_lot_nos)).delete(synchronize_session=False)
    runs = db.query(ProductionRun).filter(ProductionRun.performance_id == performance.id).all()
    for run in runs:
        run.performance_id = None
        db.delete(run)
    db.flush()
    for lot in output_lots:
        db.delete(lot)
    good_qty = float(performance.good_qty or 0)
    db.delete(performance)
    db.flush()
    if order:
        order.production_qty = max(float(order.production_qty or 0) - good_qty, 0.0)
        if order.production_qty >= float(order.order_qty or 0) and order.order_qty:
            order.status = "COMPLETED"
        elif order.production_qty > 0:
            order.status = "IN_PROGRESS"
        else:
            active_run = db.query(ProductionRun.id).filter(ProductionRun.work_order_id == order.id, ProductionRun.status == "IN_PROGRESS").first()
            order.status = "IN_PROGRESS" if active_run else "WAITING"
    return output_lot_nos


@router.get("/performance-status")
def production_performance_status(
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    performance_type: Optional[str] = Query(None),
    process_code: Optional[str] = Query(None),
    work_order_no: Optional[str] = Query(None),
    part_no: Optional[str] = Query(None),
    operator_name: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(ProductionPerformance)
    if start_date:
        query = query.filter(ProductionPerformance.performance_date >= start_date)
    if end_date:
        query = query.filter(ProductionPerformance.performance_date <= end_date)
    if performance_type:
        value = performance_type.strip().upper()
        if value not in {"MACHINING", "ASSEMBLY"}:
            raise HTTPException(400, "지원하지 않는 생산실적 구분입니다.")
        query = query.filter(ProductionPerformance.performance_type == value)
    if process_code:
        query = query.filter(ProductionPerformance.process_code == process_code.strip())
    if operator_name:
        query = query.filter(ProductionPerformance.operator_name.contains(operator_name.strip(), autoescape=True))
    rows = query.order_by(ProductionPerformance.performance_date.desc(), ProductionPerformance.id.desc()).limit(2000).all()
    if not rows:
        return []
    order_ids = {x.work_order_id for x in rows}
    order_map = {x.id: x for x in db.query(ProductionWorkOrder).filter(ProductionWorkOrder.id.in_(order_ids)).all()}
    if work_order_no:
        keyword = work_order_no.strip().lower()
        rows = [x for x in rows if x.work_order_id in order_map and keyword in order_map[x.work_order_id].work_order_no.lower()]
    if part_no:
        keyword = part_no.strip().lower()
        rows = [x for x in rows if x.work_order_id in order_map and keyword in order_map[x.work_order_id].part_no.lower()]
    part_nos = {order_map[x.work_order_id].part_no for x in rows if x.work_order_id in order_map}
    item_map = {x.part_no: x for x in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(part_nos)).all()} if part_nos else {}
    process_codes = {x.process_code for x in rows}
    process_map = {x.process_code: x for x in db.query(ProcessModel).filter(ProcessModel.process_code.in_(process_codes)).all()} if process_codes else {}
    can_delete = _is_super_admin(current_user)
    result = []
    for perf in rows:
        order = order_map.get(perf.work_order_id)
        if not order:
            continue
        item = item_map.get(order.part_no)
        process = process_map.get(perf.process_code)
        output_lots = _performance_output_lots(db, perf.id)
        output_lot_nos = [lot.lot_no for lot in output_lots]
        downstream_used = bool(_downstream_used_lots(db, output_lot_nos))
        result.append({
            "id": perf.id,
            "performance_date": perf.performance_date,
            "performance_type": perf.performance_type,
            "performance_type_name": "조립" if perf.performance_type == "ASSEMBLY" else "가공",
            "work_order_no": order.work_order_no,
            "part_no": order.part_no,
            "part_name": item.part_name if item else "",
            "process_code": perf.process_code,
            "process_name": process.process_name if process else "",
            "operator_name": perf.operator_name or "",
            "equipment_name": perf.equipment_name or perf.equipment_code or "",
            "shift_name": "주간" if perf.shift_type == "DAY" else ("야간" if perf.shift_type == "NIGHT" else ""),
            "good_qty": float(perf.good_qty or 0),
            "defect_qty": float(perf.defect_qty or 0),
            "setup_qty": float(perf.setup_qty or 0),
            "consumed_qty": float(perf.consumed_qty or 0),
            "source_lot_no": perf.source_lot_no or "",
            "output_lot_no": ",".join(output_lot_nos),
            "downstream_used": downstream_used,
            "can_delete": can_delete,
            "note": perf.note or "",
        })
    return result


@router.delete("/performances/{performance_id}")
def delete_production_performance(performance_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if not _is_super_admin(current_user):
        raise HTTPException(403, "최고관리자만 생산실적을 삭제할 수 있습니다.")
    performance = db.get(ProductionPerformance, performance_id)
    if not performance:
        raise HTTPException(404, "생산실적을 찾을 수 없습니다.")
    output_lots = _assert_performance_deletable(db, performance)
    removed_lots = _delete_performance(db, performance, output_lots)
    db.commit()
    return {"status": "success", "message": "생산실적을 삭제했습니다. 투입 원자재 LOT 사용량은 복원되고 생성 생산 LOT는 제거되었습니다.", "removed_output_lots": removed_lots}


@router.delete("/orders/{order_id}/super-delete")
def super_delete_completed_order(order_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    if not _is_super_admin(current_user):
        raise HTTPException(403, "최고관리자만 생산실적이 있는 작업지시를 삭제할 수 있습니다.")
    order = db.get(ProductionWorkOrder, order_id)
    if not order:
        raise HTTPException(404, "작업지시를 찾을 수 없습니다.")
    if order.status != "COMPLETED":
        raise HTTPException(400, "이 기능은 완료된 작업지시 삭제 전용입니다.")
    performances = db.query(ProductionPerformance).filter(ProductionPerformance.work_order_id == order.id).order_by(ProductionPerformance.id.asc()).all()
    checked = [(performance, _assert_performance_deletable(db, performance)) for performance in performances]
    for performance, output_lots in checked:
        _delete_performance(db, performance, output_lots)
    db.query(LotConsumptionModel).filter(LotConsumptionModel.work_order_id == order.id).delete(synchronize_session=False)
    remaining_runs = db.query(ProductionRun).filter(ProductionRun.work_order_id == order.id).all()
    for run in remaining_runs:
        run.performance_id = None
        db.delete(run)
    db.flush()
    db.delete(order)
    db.commit()
    return {"status": "success", "message": "완료 작업지시와 연결 생산실적을 삭제했습니다. 투입 LOT 소비수량도 복원되었습니다."}
