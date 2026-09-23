from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import StorageLocationModel
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunLotAllocation, ProductionRunMaterial
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from models.subcontract_inbound import SubcontractInboundLot, SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundLot

router = APIRouter(tags=["Subcontract Inbound Edit"])
templates = Jinja2Templates(directory="templates")


class EditLotInput(BaseModel):
    id: int = Field(gt=0)
    inbound_qty: float = Field(gt=0)
    sample_qty: float = Field(default=0, ge=0)
    supplier_lot_no: Optional[str] = Field(default=None, max_length=100)


class EditInboundInput(BaseModel):
    inbound_date: str = Field(min_length=10, max_length=10)
    storage_location: str = Field(min_length=1, max_length=20)
    note: Optional[str] = Field(default=None, max_length=1000)
    lots: list[EditLotInput] = Field(min_length=1)


def _downstream_used(
    db: Session,
    lot_nos: list[str],
    exclude_subcontract_order_id: int | None = None,
) -> list[str]:
    lot_nos = [x for x in lot_nos if x]
    if not lot_nos:
        return []
    used = set()
    used.update(
        row[0] for row in db.query(LotConsumptionModel.lot_no)
        .filter(LotConsumptionModel.lot_no.in_(lot_nos)).distinct().all()
    )
    used.update(
        row[0] for row in db.query(LotRelationModel.parent_lot_no)
        .filter(LotRelationModel.parent_lot_no.in_(lot_nos)).distinct().all()
    )
    used.update(
        row[0] for row in (
            db.query(ProductionRunLotAllocation.lot_no)
            .join(ProductionRunMaterial, ProductionRunMaterial.id == ProductionRunLotAllocation.material_id)
            .join(ProductionRun, ProductionRun.id == ProductionRunMaterial.run_id)
            .filter(
                ProductionRunLotAllocation.lot_no.in_(lot_nos),
                ProductionRun.status.in_(["IN_PROGRESS", "COMPLETED"]),
            ).distinct().all()
        )
    )
    subcontract_query = (
        db.query(SubcontractLotAllocation.lot_no)
        .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
        .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
        .filter(
            SubcontractLotAllocation.lot_no.in_(lot_nos),
            SubcontractOrderMaster.status != "CANCELLED",
        )
    )
    if exclude_subcontract_order_id is not None:
        subcontract_query = subcontract_query.filter(
            SubcontractOrderMaster.id != exclude_subcontract_order_id
        )
    used.update(row[0] for row in subcontract_query.distinct().all())
    return sorted(x for x in used if x)


def _other_received_qty(db: Session, inbound_id: int, outbound_lot_id: int) -> float:
    from models.subcontract_inbound import SubcontractInboundItem

    value = (
        db.query(func.coalesce(func.sum(SubcontractInboundLot.good_qty), 0.0))
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundLot.outbound_lot_id == outbound_lot_id,
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundMaster.id != inbound_id,
        ).scalar()
        or 0.0
    )
    return float(value)


@router.get("/subcontract/inbound/edit/{inbound_id}", response_class=HTMLResponse)
def edit_page(
    inbound_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractInboundMaster, inbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 입고 내역을 찾을 수 없습니다.")
    locations = (
        db.query(StorageLocationModel)
        .filter(StorageLocationModel.is_active == "Y")
        .order_by(StorageLocationModel.sort_order, StorageLocationModel.location_code)
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="subcontract_inbound_edit.html",
        context={"request": request, "user": current_user, "inbound_id": inbound_id, "storage_locations": locations},
    )


@router.get("/api/subcontract/inbound-edit/{inbound_id}")
def get_edit_data(
    inbound_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractInboundMaster, inbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 입고 내역을 찾을 수 없습니다.")
    lots = [lot for item in master.items for lot in item.lots]
    child_lots = [lot.child_lot_no for lot in lots if lot.child_lot_no]
    used_lots = _downstream_used(db, child_lots, master.order_id)
    return {
        "id": master.id,
        "inbound_no": master.inbound_no,
        "inbound_date": master.inbound_date,
        "outbound_no": master.outbound_no,
        "order_no": master.order_no,
        "partner_name": master.partner_name,
        "processing_type_name": master.processing_type_name,
        "storage_location": master.storage_location,
        "manager_name": master.manager_name or "",
        "status": master.status,
        "note": master.note or "",
        "editable": master.status == "RECEIVED" and not used_lots,
        "blocked_lots": used_lots,
        "items": [
            {
                "item_id": item.item_id,
                "part_no": item.part_no,
                "part_name": item.part_name,
                "spec": item.spec or "",
                "unit": item.unit,
                "lots": [
                    {
                        "id": lot.id,
                        "outbound_lot_id": lot.outbound_lot_id,
                        "source_lot_no": lot.source_lot_no,
                        "child_lot_no": lot.child_lot_no or "",
                        "source_qty": float(lot.source_qty or 0),
                        "inbound_qty": float(lot.good_qty or 0),
                        "sample_qty": float(lot.sample_qty or 0),
                        "supplier_lot_no": lot.supplier_lot_no or "",
                        "can_change_qty": bool(lot.child_lot_no and lot.child_lot_no != lot.source_lot_no),
                    }
                    for lot in item.lots
                ],
            }
            for item in master.items
        ],
    }


@router.put("/api/subcontract/inbound-edit/{inbound_id}")
def update_inbound(
    inbound_id: int,
    payload: EditInboundInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractInboundMaster, inbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 입고 내역을 찾을 수 없습니다.")
    if master.status != "RECEIVED":
        raise HTTPException(409, "입고완료 상태만 수정할 수 있습니다.")

    location = db.query(StorageLocationModel).filter(
        StorageLocationModel.location_code == payload.storage_location,
        StorageLocationModel.is_active == "Y",
    ).first()
    if location is None:
        raise HTTPException(422, "등록된 활성 입고 저장위치를 선택하세요.")

    existing_lots = [lot for item in master.items for lot in item.lots]
    existing_by_id = {lot.id: lot for lot in existing_lots}
    incoming_ids = {row.id for row in payload.lots}
    if incoming_ids != set(existing_by_id):
        raise HTTPException(422, "기존 입고 LOT 구성은 수정할 수 없습니다. 수량/외주 LOT/샘플만 수정하세요.")

    child_lots = [lot.child_lot_no for lot in existing_lots if lot.child_lot_no]
    used_lots = _downstream_used(db, child_lots, master.order_id)
    if used_lots:
        raise HTTPException(
            409,
            "후공정에서 이미 사용된 입고 LOT가 있어 수정할 수 없습니다: " + ", ".join(used_lots),
        )

    for row in payload.lots:
        lot = existing_by_id[row.id]
        outbound_lot = db.get(SubcontractOutboundLot, lot.outbound_lot_id)
        if outbound_lot is None:
            raise HTTPException(409, f"출고 LOT 정보를 찾을 수 없습니다: {lot.source_lot_no}")

        allocated = float(outbound_lot.outbound_qty or lot.source_qty or 0)
        other_received = _other_received_qty(db, master.id, lot.outbound_lot_id)
        max_qty = max(allocated - other_received, 0.0)
        if row.inbound_qty > max_qty + 1e-9:
            raise HTTPException(422, f"{lot.source_lot_no} 입고수량은 수정 가능수량 {max_qty:g}을 초과할 수 없습니다.")
        if row.sample_qty > row.inbound_qty:
            raise HTTPException(422, f"{lot.source_lot_no} 샘플수량은 입고수량을 초과할 수 없습니다.")
        if not (row.supplier_lot_no or "").strip():
            raise HTTPException(422, f"{lot.source_lot_no}의 공급사 외주 LOT를 입력해 주세요.")

        original_qty = float(lot.good_qty or 0)
        if lot.child_lot_no == lot.source_lot_no and abs(row.inbound_qty - original_qty) > 1e-9:
            raise HTTPException(
                409,
                f"{lot.source_lot_no}는 전량입고로 원 LOT를 유지한 건이라 수량 변경은 불가합니다. 입고취소 후 다시 입고하세요.",
            )

        lot.good_qty = float(row.inbound_qty)
        lot.sample_qty = float(row.sample_qty)
        lot.supplier_lot_no = (row.supplier_lot_no or "").strip() or None

        if lot.child_lot_no:
            stock = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot.child_lot_no).one_or_none()
            if stock is not None:
                stock.lot_qty = float(row.inbound_qty)
                stock.storage_location = payload.storage_location
                stock.status = "ACTIVE"
            if lot.child_lot_no != lot.source_lot_no:
                relation = db.query(LotRelationModel).filter(
                    LotRelationModel.parent_lot_no == lot.source_lot_no,
                    LotRelationModel.child_lot_no == lot.child_lot_no,
                    LotRelationModel.process_code == master.processing_type_code,
                ).one_or_none()
                if relation is not None:
                    relation.consumed_qty = float(row.inbound_qty)

    for item in master.items:
        total = sum(float(lot.good_qty or 0) for lot in item.lots)
        item.outbound_qty = total
        item.good_qty = total

    master.inbound_date = payload.inbound_date
    master.storage_location = payload.storage_location
    master.note = payload.note or None
    db.commit()
    return {"status": "success", "message": f"{master.inbound_no} 외주가공 입고를 수정했습니다."}
