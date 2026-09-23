from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.models import StorageLocationModel
from models.production_lot import ProductionLotModel
from models.subcontract_inbound import (
    SubcontractInboundItem,
    SubcontractInboundLot,
    SubcontractInboundMaster,
)
from models.subcontract_outbound import (
    SubcontractOutboundItem,
    SubcontractOutboundLot,
    SubcontractOutboundMaster,
)
from services.lot_service import LOT_PREFIXES, next_lot_no

router = APIRouter(prefix="/api/subcontract/inbound", tags=["Subcontract Inbound"])


class InboundLotResultInput(BaseModel):
    outbound_lot_id: int = Field(gt=0)
    inbound_qty: float = Field(gt=0)
    sample_qty: float = Field(default=0, ge=0)
    supplier_lot_no: Optional[str] = Field(default=None, max_length=100)


class InboundCreateInput(BaseModel):
    outbound_id: int = Field(gt=0)
    inbound_date: str = Field(min_length=10, max_length=10)
    storage_location: str = Field(min_length=1, max_length=20)
    note: Optional[str] = Field(default=None, max_length=1000)
    lot_results: list[InboundLotResultInput] = Field(min_length=1)


def _next_inbound_no(db: Session, inbound_date: str) -> str:
    yymmdd = inbound_date.replace("-", "")[2:]
    prefix = f"I{yymmdd}"
    latest = (
        db.query(SubcontractInboundMaster.inbound_no)
        .filter(SubcontractInboundMaster.inbound_no.like(prefix + "%"))
        .order_by(SubcontractInboundMaster.inbound_no.desc())
        .first()
    )
    sequence = 1
    if latest:
        try:
            sequence = int(latest[0][-3:]) + 1
        except (ValueError, TypeError):
            sequence = 1
    return f"{prefix}{sequence:03d}"


def _validate_storage(db: Session, location_code: str):
    row = (
        db.query(StorageLocationModel)
        .filter(
            StorageLocationModel.location_code == location_code,
            StorageLocationModel.is_active == "Y",
        )
        .first()
    )
    if row is None:
        raise HTTPException(422, "등록된 활성 입고 저장위치를 선택하세요.")
    return row


def _received_qty(db: Session, outbound_lot_id: int) -> float:
    value = (
        db.query(func.coalesce(func.sum(SubcontractInboundLot.good_qty), 0.0))
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundLot.outbound_lot_id == outbound_lot_id,
            SubcontractInboundMaster.status == "RECEIVED",
        )
        .scalar()
        or 0.0
    )
    return float(value)


def _inbound_history(db: Session, outbound_id: int):
    return (
        db.query(SubcontractInboundMaster)
        .filter(SubcontractInboundMaster.outbound_id == outbound_id)
        .order_by(SubcontractInboundMaster.id.desc())
        .all()
    )


def _serialize(master: SubcontractInboundMaster | None):
    if master is None:
        return None
    return {
        "id": master.id,
        "inbound_no": master.inbound_no,
        "inbound_date": master.inbound_date,
        "outbound_id": master.outbound_id,
        "outbound_no": master.outbound_no,
        "order_id": master.order_id,
        "order_no": master.order_no,
        "partner_id": master.partner_id,
        "partner_name": master.partner_name,
        "processing_type_code": master.processing_type_code,
        "processing_type_name": master.processing_type_name,
        "storage_location": master.storage_location,
        "manager_name": master.manager_name or "",
        "status": master.status,
        "status_name": "입고완료" if master.status == "RECEIVED" else "입고취소",
        "note": master.note or "",
        "created_by": master.created_by or "",
        "created_at": master.created_at.isoformat(timespec="seconds") if master.created_at else "",
        "items": [
            {
                "id": item.id,
                "outbound_item_id": item.outbound_item_id,
                "order_item_id": item.order_item_id,
                "previous_part_no": item.previous_part_no,
                "part_no": item.part_no,
                "part_name": item.part_name,
                "spec": item.spec or "",
                "unit": item.unit,
                "inbound_qty": float(item.good_qty or 0),
                "note": item.note or "",
                "lots": [
                    {
                        "id": lot.id,
                        "outbound_lot_id": lot.outbound_lot_id,
                        "source_lot_no": lot.source_lot_no,
                        "allocated_qty": float(lot.source_qty or 0),
                        "inbound_qty": float(lot.good_qty or 0),
                        "supplier_lot_no": lot.supplier_lot_no or "",
                        "sample_qty": float(lot.sample_qty or 0),
                        "child_lot_no": lot.child_lot_no or "",
                    }
                    for lot in item.lots
                ],
            }
            for item in master.items
        ],
    }


def _serialize_outbound_source(db: Session, outbound: SubcontractOutboundMaster):
    items = []
    total_allocated = Decimal("0")
    total_received = Decimal("0")
    for item in outbound.items:
        lots = []
        for lot in item.lots:
            allocated = Decimal(str(lot.outbound_qty or 0))
            received = Decimal(str(_received_qty(db, lot.id)))
            remaining = max(Decimal("0"), allocated - received)
            total_allocated += allocated
            total_received += received
            lots.append({
                "outbound_lot_id": lot.id,
                "source_lot_no": lot.lot_no,
                "allocated_qty": float(allocated),
                "received_qty": float(received),
                "remaining_qty": float(remaining),
            })
        items.append({
            "outbound_item_id": item.id,
            "order_item_id": item.order_item_id,
            "previous_part_no": item.previous_part_no,
            "part_no": item.order_part_no,
            "part_name": item.order_part_name,
            "spec": item.spec or "",
            "unit": item.unit,
            "outbound_qty": float(item.outbound_qty or 0),
            "lots": lots,
        })

    history = [_serialize(row) for row in _inbound_history(db, outbound.id)]
    return {
        "outbound_id": outbound.id,
        "outbound_no": outbound.outbound_no,
        "outbound_date": outbound.outbound_date,
        "order_id": outbound.order_id,
        "order_no": outbound.order_no,
        "partner_id": outbound.partner_id,
        "partner_name": outbound.partner_name,
        "processing_type_code": outbound.processing_type_code,
        "processing_type_name": outbound.processing_type_name,
        "external_storage_location": outbound.external_storage_location,
        "manager_name": outbound.manager_name or "",
        "status": outbound.status,
        "note": outbound.note or "",
        "total_allocated_qty": float(total_allocated),
        "total_received_qty": float(total_received),
        "total_remaining_qty": float(total_allocated - total_received),
        "inbound_status": "COMPLETED" if total_received >= total_allocated and total_allocated > 0 else ("PARTIAL" if total_received > 0 else "WAITING"),
        "items": items,
        "inbounds": history,
        "inbound": history[0] if history else None,
    }


@router.get("/outbounds")
def inbound_outbound_list(
    keyword: Optional[str] = Query(None, max_length=100),
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(SubcontractOutboundMaster).filter(SubcontractOutboundMaster.status == "OUTBOUND")
    if keyword:
        q = keyword.strip()
        query = query.filter(
            (SubcontractOutboundMaster.outbound_no.contains(q, autoescape=True))
            | (SubcontractOutboundMaster.order_no.contains(q, autoescape=True))
            | (SubcontractOutboundMaster.partner_name.contains(q, autoescape=True))
        )
    rows = query.order_by(SubcontractOutboundMaster.outbound_date.desc(), SubcontractOutboundMaster.id.desc()).limit(limit).all()
    result = []
    for outbound in rows:
        source = _serialize_outbound_source(db, outbound)
        status_name = {"WAITING": "입고대기", "PARTIAL": "부분입고", "COMPLETED": "입고완료"}[source["inbound_status"]]
        result.append({
            "outbound_id": outbound.id,
            "outbound_no": outbound.outbound_no,
            "outbound_date": outbound.outbound_date,
            "order_no": outbound.order_no,
            "partner_name": outbound.partner_name,
            "processing_type_name": outbound.processing_type_name,
            "item_count": len(outbound.items),
            "total_qty": source["total_allocated_qty"],
            "received_qty": source["total_received_qty"],
            "remaining_qty": source["total_remaining_qty"],
            "inbound_status": source["inbound_status"],
            "inbound_status_name": status_name,
        })
    return {"total": len(result), "items": result}


@router.get("/lookup")
def lookup_inbound(
    inbound_no: str = Query(..., min_length=1, max_length=20),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = inbound_no.strip()
    rows = (
        db.query(SubcontractInboundMaster)
        .filter(SubcontractInboundMaster.inbound_no.contains(keyword, autoescape=True))
        .order_by(SubcontractInboundMaster.inbound_date.desc(), SubcontractInboundMaster.id.desc())
        .limit(20)
        .all()
    )
    return {
        "total": len(rows),
        "items": [
            {
                "id": row.id,
                "inbound_no": row.inbound_no,
                "inbound_date": row.inbound_date,
                "outbound_id": row.outbound_id,
                "outbound_no": row.outbound_no,
                "order_no": row.order_no,
                "partner_name": row.partner_name,
                "processing_type_name": row.processing_type_name,
                "status": row.status,
                "status_name": "입고완료" if row.status == "RECEIVED" else "입고취소",
            }
            for row in rows
        ],
    }


@router.get("/outbound/{outbound_id}")
def get_inbound_source(
    outbound_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    outbound = db.get(SubcontractOutboundMaster, outbound_id)
    if outbound is None:
        raise HTTPException(404, "외주가공 출고 내역을 찾을 수 없습니다.")
    if outbound.status != "OUTBOUND":
        raise HTTPException(409, "출고완료 상태의 외주가공 건만 입고할 수 있습니다.")
    return _serialize_outbound_source(db, outbound)


@router.get("/{inbound_id}")
def get_inbound(
    inbound_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractInboundMaster, inbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 입고 내역을 찾을 수 없습니다.")
    return _serialize(master)


@router.post("")
def create_inbound(
    payload: InboundCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    outbound = db.get(SubcontractOutboundMaster, payload.outbound_id)
    if outbound is None:
        raise HTTPException(404, "외주가공 출고 내역을 찾을 수 없습니다.")
    if outbound.status != "OUTBOUND":
        raise HTTPException(409, "출고완료 상태의 외주가공 건만 입고할 수 있습니다.")
    if not outbound.items:
        raise HTTPException(422, "입고할 외주가공 출고 품목이 없습니다.")
    _validate_storage(db, payload.storage_location)

    lot_by_id = {lot.id: (item, lot) for item in outbound.items for lot in item.lots}
    seen = set()
    validated = []
    for row in payload.lot_results:
        if row.outbound_lot_id in seen:
            raise HTTPException(422, "동일한 출고 LOT를 한 번의 입고에 중복 입력할 수 없습니다.")
        seen.add(row.outbound_lot_id)
        source = lot_by_id.get(row.outbound_lot_id)
        if source is None:
            raise HTTPException(422, "선택한 외주 출고에 포함되지 않은 LOT가 있습니다.")
        source_item, source_lot = source
        allocated = Decimal(str(source_lot.outbound_qty or 0))
        received = Decimal(str(_received_qty(db, source_lot.id)))
        remaining = allocated - received
        inbound_qty = Decimal(str(row.inbound_qty))
        sample_qty = Decimal(str(row.sample_qty))
        if remaining <= 0:
            raise HTTPException(409, f"{source_lot.lot_no}는 이미 전량 입고되었습니다.")
        if inbound_qty > remaining:
            raise HTTPException(422, f"{source_lot.lot_no}의 입고수량은 잔여수량 {float(remaining):g}을 초과할 수 없습니다.")
        if sample_qty > inbound_qty:
            raise HTTPException(422, f"{source_lot.lot_no}의 샘플수량은 금회 입고수량을 초과할 수 없습니다.")
        if not (row.supplier_lot_no or "").strip():
            raise HTTPException(422, f"{source_lot.lot_no}의 공급사 외주 LOT를 입력해 주세요.")
        validated.append((row, source_item, source_lot, allocated, received, inbound_qty))

    inbound_no = _next_inbound_no(db, payload.inbound_date)
    master = SubcontractInboundMaster(
        inbound_no=inbound_no,
        inbound_date=payload.inbound_date,
        outbound_id=outbound.id,
        outbound_no=outbound.outbound_no,
        order_id=outbound.order_id,
        order_no=outbound.order_no,
        partner_id=outbound.partner_id,
        partner_name=outbound.partner_name,
        processing_type_code=outbound.processing_type_code,
        processing_type_name=outbound.processing_type_name,
        storage_location=payload.storage_location,
        manager_name=outbound.manager_name,
        status="RECEIVED",
        note=payload.note or outbound.note,
        created_by=getattr(current_user, "username", None),
    )

    item_rows = {}
    reserved_lots = set()
    for row, source_item, source_lot, allocated, received, inbound_qty in validated:
        inbound_item = item_rows.get(source_item.id)
        if inbound_item is None:
            inbound_item = SubcontractInboundItem(
                outbound_item_id=source_item.id,
                order_item_id=source_item.order_item_id,
                previous_item_id=source_item.previous_item_id,
                item_id=source_item.item_id,
                previous_part_no=source_item.previous_part_no,
                part_no=source_item.order_part_no,
                part_name=source_item.order_part_name,
                spec=source_item.spec,
                unit=source_item.unit,
                outbound_qty=0,
                good_qty=0,
                defect_qty=0,
                note=source_item.note,
            )
            item_rows[source_item.id] = inbound_item
            master.items.append(inbound_item)


        # 외주가공 범용 정책:
        # 첫 입고가 전량이면 기존 LOT를 유지하고, 부분입고가 발생하면 입고분마다 LZ LOT를 새로 생성합니다.
        # LZ는 은도금 전용이 아니라 외주가공 범용 신규 LOT Prefix로 사용합니다.
        is_split = received > 0 or inbound_qty < allocated
        if is_split:
            child_lot_no = next_lot_no(db, LOT_PREFIXES["OUTSOURCE"], payload.inbound_date, 1, reserved_lots)
            reserved_lots.add(child_lot_no)
            db.add(LotRelationModel(
                parent_lot_no=source_lot.lot_no,
                child_lot_no=child_lot_no,
                process_code=outbound.processing_type_code,
                consumed_qty=float(inbound_qty),
            ))
            db.add(ProductionLotModel(
                lot_no=child_lot_no,
                item_id=source_item.item_id,
                part_no=source_item.order_part_no,
                lot_qty=float(inbound_qty),
                storage_location=payload.storage_location,
                status="ACTIVE",
                note=f"외주가공 부분입고 {inbound_no} / 원LOT {source_lot.lot_no}",
            ))
        else:
            child_lot_no = source_lot.lot_no
            stock = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == child_lot_no).one_or_none()
            if stock is None:
                db.add(ProductionLotModel(
                    lot_no=child_lot_no,
                    item_id=source_item.item_id,
                    part_no=source_item.order_part_no,
                    lot_qty=float(inbound_qty),
                    storage_location=payload.storage_location,
                    status="ACTIVE",
                    note=f"외주가공 전량입고 {inbound_no} / LOT 유지",
                ))
            else:
                stock.item_id = source_item.item_id
                stock.part_no = source_item.order_part_no
                stock.lot_qty = float(inbound_qty)
                stock.storage_location = payload.storage_location
                stock.status = "ACTIVE"
                stock.note = f"외주가공 전량입고 {inbound_no} / LOT 유지"

        inbound_item.lots.append(SubcontractInboundLot(
            outbound_lot_id=source_lot.id,
            source_lot_no=source_lot.lot_no,
            source_qty=float(allocated),
            good_qty=float(inbound_qty),
            defect_qty=0,
            defect_type=None,
            child_lot_no=child_lot_no,
            supplier_lot_no=(row.supplier_lot_no or "").strip() or None,
            sample_qty=float(row.sample_qty),
        ))
        inbound_item.outbound_qty += float(inbound_qty)
        inbound_item.good_qty += float(inbound_qty)

    db.add(master)
    db.commit()
    db.refresh(master)
    return _serialize(master)


@router.post("/{inbound_id}/cancel")
def cancel_inbound(
    inbound_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractInboundMaster, inbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 입고 내역을 찾을 수 없습니다.")
    if master.status == "CANCELLED":
        return _serialize(master)

    split_lots = [
        lot.child_lot_no
        for item in master.items
        for lot in item.lots
        if lot.child_lot_no and lot.child_lot_no != lot.source_lot_no
    ]
    if split_lots:
        downstream = (
            db.query(LotRelationModel.parent_lot_no)
            .filter(LotRelationModel.parent_lot_no.in_(split_lots))
            .distinct()
            .all()
        )
        if downstream:
            raise HTTPException(
                409,
                "후공정에서 이미 사용된 입고 LOT가 있어 취소할 수 없습니다: "
                + ", ".join(sorted(row[0] for row in downstream)),
            )
        db.query(ProductionLotModel).filter(ProductionLotModel.lot_no.in_(split_lots)).delete(synchronize_session=False)
        db.query(LotRelationModel).filter(
            LotRelationModel.child_lot_no.in_(split_lots),
            LotRelationModel.process_code == master.processing_type_code,
        ).delete(synchronize_session=False)

    # 전량입고로 원 LOT를 유지한 건은 동일 LOT의 품번만 원상복구합니다.
    for item in master.items:
        for lot in item.lots:
            if lot.child_lot_no and lot.child_lot_no == lot.source_lot_no:
                downstream = db.query(LotRelationModel.id).filter(LotRelationModel.parent_lot_no == lot.child_lot_no).first()
                if downstream:
                    raise HTTPException(409, f"후공정에서 이미 사용된 LOT가 있어 취소할 수 없습니다: {lot.child_lot_no}")
                stock = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot.child_lot_no).one_or_none()
                if stock is not None:
                    stock.item_id = item.previous_item_id
                    stock.part_no = item.previous_part_no
                    stock.lot_qty = float(lot.source_qty or 0)
                    stock.status = "ACTIVE"
                    stock.note = f"외주가공 입고취소 {master.inbound_no}"

    master.status = "CANCELLED"
    master.cancelled_by = getattr(current_user, "username", None)
    master.cancelled_at = datetime.now()
    db.commit()
    db.refresh(master)
    return _serialize(master)
