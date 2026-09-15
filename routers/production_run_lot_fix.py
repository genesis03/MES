from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import PurchaseInboundItem, PurchaseInboundMaster
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster

router = APIRouter(prefix="/api/production-run", tags=["Production Run LOT Fix"])


class ScanLotPayload(BaseModel):
    lot_no: str


def _lot_rows(db: Session, part_no: str):
    rows = []
    purchases = (
        db.query(PurchaseInboundItem)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.part_no == part_no,
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .all()
    )
    for row in purchases:
        rows.append({
            "lot_no": row.internal_lot_no,
            "part_no": row.part_no,
            "base_qty": float(row.inbound_qty or 0),
            "storage_location": row.storage_location or "",
            "source_type": "PURCHASE",
        })

    productions = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.part_no == part_no, ProductionLotModel.status == "ACTIVE")
        .all()
    )
    for row in productions:
        rows.append({
            "lot_no": row.lot_no,
            "part_no": row.part_no,
            "base_qty": float(row.lot_qty or 0),
            "storage_location": row.storage_location or "",
            "source_type": "PRODUCTION",
        })

    rows.sort(key=lambda x: x["lot_no"])
    return rows


def _returned_from_subcontract(db: Session, lot_no: str) -> bool:
    """외주가공 전량입고 후 동일 LOT 번호가 후공정 품번으로 복귀했는지 확인합니다."""
    return (
        db.query(SubcontractInboundLot.id)
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.child_lot_no == lot_no,
            SubcontractInboundLot.source_lot_no == lot_no,
        )
        .first()
        is not None
    )


def _available_qty(db: Session, lot_no: str, base_qty: float, current_run_id: Optional[int] = None) -> float:
    consumed_process = (
        db.query(func.coalesce(func.sum(LotConsumptionModel.consumed_qty), 0.0))
        .filter(LotConsumptionModel.lot_no == lot_no)
        .scalar()
        or 0.0
    )
    consumed_relation = (
        db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0))
        .filter(LotRelationModel.parent_lot_no == lot_no)
        .scalar()
        or 0.0
    )

    # 외주가공 전량입고로 동일 LOT가 가공완료 품번으로 복귀한 경우에는
    # 과거 외주발주의 LOT 배정을 현재 재고 예약으로 다시 차감하면 안 됩니다.
    if _returned_from_subcontract(db, lot_no):
        reserved_subcontract = 0.0
    else:
        reserved_subcontract = (
            db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
            .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
            .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
            .filter(
                SubcontractLotAllocation.lot_no == lot_no,
                SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]),
            )
            .scalar()
            or 0.0
        )

    run_query = (
        db.query(func.coalesce(func.sum(ProductionRunLotAllocation.allocated_qty), 0.0))
        .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
        .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
        .filter(
            ProductionRunLotAllocation.lot_no == lot_no,
            ProductionRun.status == "IN_PROGRESS",
        )
    )
    if current_run_id:
        run_query = run_query.filter(ProductionRun.id != current_run_id)
    reserved_run = run_query.scalar() or 0.0

    return max(
        float(base_qty)
        - float(consumed_process)
        - float(consumed_relation)
        - float(reserved_subcontract)
        - float(reserved_run),
        0.0,
    )


def _serialize_material(material: ProductionRunMaterial):
    allocated = sum(float(x.allocated_qty or 0) for x in material.allocations)
    return {
        "id": material.id,
        "part_no": material.material_part_no,
        "part_name": material.material_name or "",
        "unit": material.unit,
        "bom_qty": material.bom_qty,
        "required_qty": material.required_qty,
        "allocated_qty": allocated,
        "remaining_qty": max(float(material.required_qty or 0) - allocated, 0.0),
        "complete": abs(allocated - float(material.required_qty or 0)) < 1e-9 and float(material.required_qty or 0) > 0,
        "allocations": [
            {
                "lot_no": x.lot_no,
                "allocated_qty": x.allocated_qty,
                "source_type": x.source_type or "",
                "storage_location": x.storage_location or "",
            }
            for x in material.allocations
        ],
    }


@router.post("/{run_id}/scan-lot")
def scan_lot(
    run_id: int,
    payload: ScanLotPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    run = db.get(ProductionRun, run_id)
    if run is None:
        raise HTTPException(404, "가동내역을 찾을 수 없습니다.")
    if run.status != "IN_PROGRESS":
        raise HTTPException(400, "생산중 가동내역만 LOT를 배정할 수 있습니다.")

    scanned = payload.lot_no.strip()
    if not scanned:
        raise HTTPException(400, "LOT 번호를 입력하세요.")

    matched_material = None
    matched_lot = None
    for material in run.materials:
        for lot in _lot_rows(db, material.material_part_no):
            if str(lot["lot_no"]).upper() == scanned.upper():
                matched_material = material
                matched_lot = lot
                break
        if matched_material:
            break

    if not matched_material or not matched_lot:
        raise HTTPException(404, "이 작업의 BOM 자재에 해당하는 LOT가 아닙니다.")

    required = float(matched_material.required_qty or 0)
    allocated = sum(float(x.allocated_qty or 0) for x in matched_material.allocations)
    remaining = max(required - allocated, 0.0)
    if remaining <= 1e-9:
        raise HTTPException(409, f"{matched_material.material_part_no}는 이미 필요수량이 모두 배정되었습니다.")

    fifo_lot = None
    fifo_available = 0.0
    for lot in _lot_rows(db, matched_material.material_part_no):
        if any(x.lot_no == lot["lot_no"] for x in matched_material.allocations):
            continue
        available = _available_qty(db, lot["lot_no"], lot["base_qty"], current_run_id=run.id)
        if available > 1e-9:
            fifo_lot = lot
            fifo_available = available
            break

    if not fifo_lot:
        raise HTTPException(409, "선입선출 기준으로 배정 가능한 LOT 재고가 없습니다.")

    assign_qty = min(remaining, fifo_available)
    matched_material.allocations.append(
        ProductionRunLotAllocation(
            lot_no=fifo_lot["lot_no"],
            allocated_qty=assign_qty,
            source_type=fifo_lot["source_type"],
            storage_location=fifo_lot["storage_location"],
        )
    )
    db.commit()

    message = f"{fifo_lot['lot_no']}에 {assign_qty:g} 배정했습니다."
    if str(fifo_lot["lot_no"]).upper() != scanned.upper():
        message = f"스캔 LOT {scanned}보다 선입 LOT {fifo_lot['lot_no']}를 우선 배정했습니다. ({assign_qty:g})"

    return {"message": message, "material": _serialize_material(matched_material)}
