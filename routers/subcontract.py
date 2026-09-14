from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.models import ItemMasterModel, ProcessModel, PurchaseInboundItem, PurchaseInboundMaster, StorageLocationModel
from models.partner import Partner
from models.subcontract import SubcontractLotAllocation, SubcontractOrderItem, SubcontractOrderMaster
from schemas.subcontract import LotAllocationInput, SubcontractOrderInput

router = APIRouter(prefix="/api/subcontract", tags=["Subcontract"])


def _next_order_no(db: Session, order_date: str) -> str:
    prefix = f"SO-{order_date.replace('-', '')}-"
    latest = (
        db.query(SubcontractOrderMaster.order_no)
        .filter(SubcontractOrderMaster.order_no.like(prefix + "%"))
        .order_by(SubcontractOrderMaster.order_no.desc())
        .first()
    )
    sequence = 1
    if latest:
        try:
            sequence = int(latest[0].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            sequence = 1
    return f"{prefix}{sequence:03d}"


def _processing(db: Session, code: str) -> ProcessModel:
    row = (
        db.query(ProcessModel)
        .filter(ProcessModel.process_code == code, ProcessModel.is_active == "Y")
        .first()
    )
    if row is None:
        raise HTTPException(422, "등록된 활성 가공유형을 선택하세요.")
    return row


def _derive_order_part(previous_part_no: str, processing_name: str, requested: str | None) -> str:
    compact_name = (processing_name or "").replace(" ", "")
    if "은도금" in compact_name:
        return previous_part_no if previous_part_no.endswith("-Ag") else previous_part_no + "-Ag"
    return (requested or previous_part_no).strip()


def _validate_header(db: Session, payload: SubcontractOrderInput):
    partner = db.get(Partner, payload.partner_id)
    if partner is None or partner.is_active != "Y" or partner.partner_type not in ("VENDOR", "BOTH"):
        raise HTTPException(422, "등록된 활성 외주 발주처를 선택하세요.")
    if partner.partner_name != payload.partner_name:
        raise HTTPException(422, "발주처 정보가 일치하지 않습니다.")
    processing = _processing(db, payload.processing_type_code)
    location = (
        db.query(StorageLocationModel)
        .filter(
            StorageLocationModel.location_code == payload.external_storage_location,
            StorageLocationModel.is_active == "Y",
        )
        .first()
    )
    if location is None:
        raise HTTPException(422, "등록된 활성 외주 저장위치를 선택하세요.")
    return partner, processing


def _available_purchase_lots(db: Session, part_no: str, current_item_id: int | None = None):
    """현재 코드에서 확인 가능한 확정 구매 LOT를 반환합니다.

    생산 LOT 원장이 붙으면 이 함수의 공급원만 확장하면 외주 발주 화면은 그대로 사용할 수 있습니다.
    """
    rows = (
        db.query(PurchaseInboundItem)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.part_no == part_no,
            PurchaseInboundItem.internal_lot_no.isnot(None),
            PurchaseInboundItem.internal_lot_no != "",
        )
        .order_by(PurchaseInboundItem.id)
        .all()
    )
    result = []
    for inbound_item in rows:
        lot_no = inbound_item.internal_lot_no
        consumed = (
            db.query(func.coalesce(func.sum(LotRelationModel.consumed_qty), 0.0))
            .filter(LotRelationModel.parent_lot_no == lot_no)
            .scalar()
            or 0.0
        )
        reserved_query = (
            db.query(func.coalesce(func.sum(SubcontractLotAllocation.allocated_qty), 0.0))
            .join(SubcontractOrderItem, SubcontractOrderItem.id == SubcontractLotAllocation.order_item_id)
            .join(SubcontractOrderMaster, SubcontractOrderMaster.id == SubcontractOrderItem.order_id)
            .filter(
                SubcontractLotAllocation.lot_no == lot_no,
                SubcontractOrderMaster.status.in_(["DRAFT", "LOT_ALLOCATING", "ORDERED"]),
            )
        )
        if current_item_id:
            reserved_query = reserved_query.filter(SubcontractLotAllocation.order_item_id != current_item_id)
        reserved = reserved_query.scalar() or 0.0
        available = float(Decimal(str(inbound_item.inbound_qty)) - Decimal(str(consumed)) - Decimal(str(reserved)))
        if available > 0:
            result.append({
                "lot_no": lot_no,
                "part_no": inbound_item.part_no,
                "lot_qty": available,
                "storage_location": inbound_item.storage_location,
                "source": "PURCHASE",
            })
    return result


def _serialize_order(master: SubcontractOrderMaster):
    items = []
    all_allocated = True
    for item in master.items:
        allocations = [
            {
                "id": row.id,
                "lot_no": row.lot_no,
                "lot_qty": row.lot_qty,
                "allocated_qty": row.allocated_qty,
            }
            for row in item.allocations
        ]
        allocated_qty = sum(float(row["allocated_qty"]) for row in allocations)
        allocation_complete = abs(allocated_qty - float(item.order_qty)) < 1e-9 and bool(allocations)
        all_allocated = all_allocated and allocation_complete
        items.append({
            "id": item.id,
            "previous_part_no": item.previous_part_no,
            "order_part_no": item.order_part_no,
            "order_part_name": item.order_part_name,
            "spec": item.spec or "",
            "unit": item.unit,
            "processing_type_code": item.processing_type_code,
            "processing_type_name": item.processing_type_name,
            "order_qty": item.order_qty,
            "delivery_date": item.delivery_date or "",
            "note": item.note or "",
            "allocated_qty": allocated_qty,
            "allocation_complete": allocation_complete,
            "allocations": allocations,
        })
    return {
        "id": master.id,
        "order_no": master.order_no,
        "order_date": master.order_date,
        "partner_id": master.partner_id,
        "partner_name": master.partner_name,
        "processing_type_code": master.processing_type_code,
        "processing_type_name": master.processing_type_name,
        "delivery_due_date": master.delivery_due_date or "",
        "external_storage_location": master.external_storage_location,
        "manager_name": master.manager_name or "",
        "status": master.status,
        "note": master.note or "",
        "items": items,
        "can_confirm": bool(items) and all_allocated and master.status != "ORDERED",
    }


@router.get("/stock")
def subcontract_stock(
    part_no: str = Query(..., min_length=1, max_length=50),
    order_item_id: int | None = Query(None, gt=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    part = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no).first()
    if part is None:
        raise HTTPException(404, "품목 마스터에서 이전 품번을 찾을 수 없습니다.")
    lots = _available_purchase_lots(db, part_no, order_item_id)
    return {
        "part_no": part_no,
        "stock_qty": sum(float(row["lot_qty"]) for row in lots),
        "lots": lots,
        "source_note": "현재 구매입고 LOT 기준 재고입니다. 생산 LOT 원장 연동 시 생산 LOT도 이 목록에 포함됩니다.",
    }


@router.post("/orders")
def create_subcontract_order(
    payload: SubcontractOrderInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    partner, header_processing = _validate_header(db, payload)
    previous_parts = {item.previous_part_no for item in payload.items}
    part_rows = {
        row.part_no: row
        for row in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(previous_parts)).all()
    }
    missing = sorted(previous_parts - set(part_rows))
    if missing:
        raise HTTPException(404, "등록되지 않은 이전 품번입니다: " + ", ".join(missing))

    master = SubcontractOrderMaster(
        order_no=_next_order_no(db, payload.order_date),
        order_date=payload.order_date,
        partner_id=partner.id,
        partner_name=partner.partner_name,
        processing_type_code=header_processing.process_code,
        processing_type_name=header_processing.process_name,
        delivery_due_date=payload.delivery_due_date,
        external_storage_location=payload.external_storage_location,
        manager_name=payload.manager_name,
        status="DRAFT",
        note=payload.note,
        created_by=getattr(current_user, "username", None),
    )
    for item_payload in payload.items:
        previous = part_rows[item_payload.previous_part_no]
        processing = _processing(db, item_payload.processing_type_code)
        order_part_no = _derive_order_part(previous.part_no, processing.process_name, item_payload.order_part_no)
        output_master = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == order_part_no).first()
        master.items.append(SubcontractOrderItem(
            previous_part_no=previous.part_no,
            order_part_no=order_part_no,
            order_part_name=(output_master.part_name if output_master else previous.part_name),
            spec=(output_master.spec if output_master else previous.spec),
            unit=(output_master.unit if output_master else previous.unit),
            processing_type_code=processing.process_code,
            processing_type_name=processing.process_name,
            order_qty=item_payload.order_qty,
            delivery_date=item_payload.delivery_date,
            note=item_payload.note,
        ))
    db.add(master)
    db.commit()
    db.refresh(master)
    return _serialize_order(master)


@router.put("/orders/{order_id}")
def update_subcontract_order(
    order_id: int,
    payload: SubcontractOrderInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractOrderMaster, order_id)
    if master is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")
    if master.status == "ORDERED":
        raise HTTPException(409, "발주 확정 후에는 LOT 배정/출고 이력 보호를 위해 직접 수정할 수 없습니다.")
    partner, header_processing = _validate_header(db, payload)
    previous_parts = {item.previous_part_no for item in payload.items}
    part_rows = {
        row.part_no: row
        for row in db.query(ItemMasterModel).filter(ItemMasterModel.part_no.in_(previous_parts)).all()
    }
    missing = sorted(previous_parts - set(part_rows))
    if missing:
        raise HTTPException(404, "등록되지 않은 이전 품번입니다: " + ", ".join(missing))

    master.order_date = payload.order_date
    master.partner_id = partner.id
    master.partner_name = partner.partner_name
    master.processing_type_code = header_processing.process_code
    master.processing_type_name = header_processing.process_name
    master.delivery_due_date = payload.delivery_due_date
    master.external_storage_location = payload.external_storage_location
    master.manager_name = payload.manager_name
    master.note = payload.note
    master.status = "DRAFT"
    master.items.clear()
    db.flush()
    for item_payload in payload.items:
        previous = part_rows[item_payload.previous_part_no]
        processing = _processing(db, item_payload.processing_type_code)
        order_part_no = _derive_order_part(previous.part_no, processing.process_name, item_payload.order_part_no)
        output_master = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == order_part_no).first()
        master.items.append(SubcontractOrderItem(
            previous_part_no=previous.part_no,
            order_part_no=order_part_no,
            order_part_name=(output_master.part_name if output_master else previous.part_name),
            spec=(output_master.spec if output_master else previous.spec),
            unit=(output_master.unit if output_master else previous.unit),
            processing_type_code=processing.process_code,
            processing_type_name=processing.process_name,
            order_qty=item_payload.order_qty,
            delivery_date=item_payload.delivery_date,
            note=item_payload.note,
        ))
    db.commit()
    db.refresh(master)
    return _serialize_order(master)


@router.get("/orders/{order_id}")
def get_subcontract_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractOrderMaster, order_id)
    if master is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")
    return _serialize_order(master)


@router.put("/orders/{order_id}/items/{item_id}/lots")
def set_subcontract_lots(
    order_id: int,
    item_id: int,
    payload: LotAllocationInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractOrderMaster, order_id)
    if master is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")
    if master.status == "ORDERED":
        raise HTTPException(409, "이미 발주 확정된 건은 LOT 배정을 변경할 수 없습니다.")
    item = db.get(SubcontractOrderItem, item_id)
    if item is None or item.order_id != master.id:
        raise HTTPException(404, "외주가공 발주 품목을 찾을 수 없습니다.")

    available = {row["lot_no"]: row for row in _available_purchase_lots(db, item.previous_part_no, item.id)}
    requested = list(dict.fromkeys(payload.lot_nos))
    missing = [lot_no for lot_no in requested if lot_no not in available]
    if missing:
        raise HTTPException(409, "현재 사용 가능한 LOT이 아닙니다: " + ", ".join(missing))

    item.allocations.clear()
    db.flush()
    total = 0.0
    for lot_no in requested:
        lot = available[lot_no]
        lot_qty = float(lot["lot_qty"])
        total += lot_qty
        item.allocations.append(SubcontractLotAllocation(
            lot_no=lot_no,
            lot_qty=lot_qty,
            allocated_qty=lot_qty,
        ))
    if total > float(item.order_qty) + 1e-9:
        raise HTTPException(422, "LOT 전체수량 합계가 발주수량을 초과합니다. LOT를 나누어 배정할 수 없습니다.")

    master.status = "LOT_ALLOCATING" if requested else "DRAFT"
    db.commit()
    db.refresh(master)
    return _serialize_order(master)


@router.post("/orders/{order_id}/confirm")
def confirm_subcontract_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractOrderMaster, order_id)
    if master is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")
    if master.status == "ORDERED":
        return _serialize_order(master)
    if not master.items:
        raise HTTPException(422, "발주 품목이 없습니다.")

    incomplete = []
    for item in master.items:
        if not item.allocations:
            incomplete.append(item.order_part_no)
            continue
        total = sum(float(row.allocated_qty) for row in item.allocations)
        if abs(total - float(item.order_qty)) >= 1e-9:
            incomplete.append(item.order_part_no)
        if any(abs(float(row.allocated_qty) - float(row.lot_qty)) >= 1e-9 for row in item.allocations):
            raise HTTPException(409, "외주 출고 LOT는 LOT 전체수량을 사용해야 합니다.")
    if incomplete:
        raise HTTPException(409, "LOT 배정수량이 발주수량과 일치해야 합니다: " + ", ".join(incomplete))

    master.status = "ORDERED"
    master.updated_at = datetime.now()
    db.commit()
    db.refresh(master)
    return _serialize_order(master)
