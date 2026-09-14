from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
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

router = APIRouter(prefix="/api/subcontract/inbound", tags=["Subcontract Inbound"])

DEFECT_TYPES = (
    "찍힘", "긁힘", "치수", "칩", "버", "소재", "오조립",
    "미도금", "얼룩", "변색", "조도", "형상", "set-up", "기타",
)


class InboundLotResultInput(BaseModel):
    outbound_lot_id: int = Field(gt=0)
    defect_qty: float = Field(default=0, ge=0)
    defect_type: Optional[str] = Field(default=None, max_length=20)


class InboundCreateInput(BaseModel):
    outbound_id: int = Field(gt=0)
    inbound_date: str = Field(min_length=10, max_length=10)
    storage_location: str = Field(min_length=1, max_length=20)
    note: Optional[str] = Field(default=None, max_length=1000)
    lot_results: list[InboundLotResultInput] = Field(default_factory=list)


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


def _active_inbound(db: Session, outbound_id: int):
    return (
        db.query(SubcontractInboundMaster)
        .filter(
            SubcontractInboundMaster.outbound_id == outbound_id,
            SubcontractInboundMaster.status == "RECEIVED",
        )
        .order_by(SubcontractInboundMaster.id.desc())
        .first()
    )


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
                "outbound_qty": float(item.outbound_qty or 0),
                "good_qty": float(item.good_qty or 0),
                "defect_qty": float(item.defect_qty or 0),
                "note": item.note or "",
                "lots": [
                    {
                        "id": lot.id,
                        "outbound_lot_id": lot.outbound_lot_id,
                        "source_lot_no": lot.source_lot_no,
                        "source_qty": float(lot.source_qty or 0),
                        "good_qty": float(lot.good_qty or 0),
                        "defect_qty": float(lot.defect_qty or 0),
                        "defect_type": lot.defect_type or "",
                        "child_lot_no": lot.child_lot_no or "",
                    }
                    for lot in item.lots
                ],
            }
            for item in master.items
        ],
    }


def _serialize_outbound_source(outbound: SubcontractOutboundMaster):
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
        "items": [
            {
                "outbound_item_id": item.id,
                "order_item_id": item.order_item_id,
                "previous_part_no": item.previous_part_no,
                "part_no": item.order_part_no,
                "part_name": item.order_part_name,
                "spec": item.spec or "",
                "unit": item.unit,
                "outbound_qty": float(item.outbound_qty or 0),
                "lots": [
                    {
                        "outbound_lot_id": lot.id,
                        "source_lot_no": lot.lot_no,
                        "source_qty": float(lot.outbound_qty or 0),
                    }
                    for lot in item.lots
                ],
            }
            for item in outbound.items
        ],
        "inbound": _serialize(_active_inbound(outbound._sa_instance_state.session, outbound.id)),
    }


@router.get("/defect-types")
def defect_types(current_user=Depends(get_current_user)):
    return {"items": list(DEFECT_TYPES)}


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
    items = []
    for outbound in rows:
        active = _active_inbound(db, outbound.id)
        total_qty = sum(float(item.outbound_qty or 0) for item in outbound.items)
        items.append({
            "outbound_id": outbound.id,
            "outbound_no": outbound.outbound_no,
            "outbound_date": outbound.outbound_date,
            "order_no": outbound.order_no,
            "partner_name": outbound.partner_name,
            "processing_type_name": outbound.processing_type_name,
            "item_count": len(outbound.items),
            "total_qty": total_qty,
            "inbound_id": active.id if active else None,
            "inbound_no": active.inbound_no if active else "",
            "inbound_date": active.inbound_date if active else "",
            "inbound_status": active.status if active else "WAITING",
            "inbound_status_name": "입고완료" if active else "입고대기",
        })
    return {"total": len(items), "items": items}


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
    data = _serialize_outbound_source(outbound)
    data["inbound"] = _serialize(_active_inbound(db, outbound.id))
    return data


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
    if _active_inbound(db, outbound.id):
        raise HTTPException(409, "이미 입고완료된 외주가공 출고 건입니다.")
    if not outbound.items:
        raise HTTPException(422, "입고할 외주가공 출고 품목이 없습니다.")
    _validate_storage(db, payload.storage_location)

    lot_result_map = {}
    for row in payload.lot_results:
        if row.outbound_lot_id in lot_result_map:
            raise HTTPException(422, "동일한 출고 LOT의 검사결과가 중복되었습니다.")
        if row.defect_qty > 0:
            if row.defect_type not in DEFECT_TYPES:
                raise HTTPException(422, "등록된 불량유형을 선택하세요.")
        elif row.defect_type:
            raise HTTPException(422, "불량수량이 0이면 불량유형을 입력하지 않습니다.")
        lot_result_map[row.outbound_lot_id] = row

    valid_lot_ids = {lot.id for item in outbound.items for lot in item.lots}
    unknown = set(lot_result_map) - valid_lot_ids
    if unknown:
        raise HTTPException(422, "선택한 외주 출고에 포함되지 않은 LOT가 있습니다.")

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

    lot_position = 0
    generated_relations = []
    generated_stock = []
    for source_item in outbound.items:
        inbound_item = SubcontractInboundItem(
            outbound_item_id=source_item.id,
            order_item_id=source_item.order_item_id,
            previous_part_no=source_item.previous_part_no,
            part_no=source_item.order_part_no,
            part_name=source_item.order_part_name,
            spec=source_item.spec,
            unit=source_item.unit,
            outbound_qty=float(source_item.outbound_qty or 0),
            good_qty=0,
            defect_qty=0,
            note=source_item.note,
        )
        item_good = Decimal("0")
        item_defect = Decimal("0")
        for source_lot in source_item.lots:
            lot_position += 1
            result = lot_result_map.get(source_lot.id)
            source_qty = Decimal(str(source_lot.outbound_qty or 0))
            defect_qty = Decimal(str(result.defect_qty if result else 0))
            if defect_qty > source_qty:
                raise HTTPException(422, f"{source_lot.lot_no}의 불량수량이 출고수량을 초과합니다.")
            good_qty = source_qty - defect_qty
            child_lot_no = f"LOT-{inbound_no}-{lot_position:03d}"
            if db.query(ProductionLotModel.id).filter(ProductionLotModel.lot_no == child_lot_no).first():
                raise HTTPException(409, f"신규 LOT 번호가 중복되었습니다: {child_lot_no}")

            inbound_item.lots.append(SubcontractInboundLot(
                outbound_lot_id=source_lot.id,
                source_lot_no=source_lot.lot_no,
                source_qty=float(source_qty),
                good_qty=float(good_qty),
                defect_qty=float(defect_qty),
                defect_type=(result.defect_type if result and defect_qty > 0 else None),
                child_lot_no=child_lot_no,
            ))
            generated_relations.append(LotRelationModel(
                parent_lot_no=source_lot.lot_no,
                child_lot_no=child_lot_no,
                process_code=outbound.processing_type_code,
                consumed_qty=float(source_qty),
            ))
            if good_qty > 0:
                generated_stock.append(ProductionLotModel(
                    lot_no=child_lot_no,
                    part_no=source_item.order_part_no,
                    lot_qty=float(good_qty),
                    storage_location=payload.storage_location,
                    status="ACTIVE",
                    note=f"외주가공 입고 {inbound_no} / 원LOT {source_lot.lot_no}",
                ))
            item_good += good_qty
            item_defect += defect_qty

        if item_good + item_defect != Decimal(str(source_item.outbound_qty or 0)):
            raise HTTPException(409, f"{source_item.order_part_no}의 출고 LOT 합계와 입고 대상 수량이 일치하지 않습니다.")
        inbound_item.good_qty = float(item_good)
        inbound_item.defect_qty = float(item_defect)
        master.items.append(inbound_item)

    db.add(master)
    db.add_all(generated_relations)
    db.add_all(generated_stock)
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

    child_lots = [lot.child_lot_no for item in master.items for lot in item.lots if lot.child_lot_no]
    if child_lots:
        downstream = (
            db.query(LotRelationModel.parent_lot_no)
            .filter(LotRelationModel.parent_lot_no.in_(child_lots))
            .distinct()
            .all()
        )
        if downstream:
            raise HTTPException(
                409,
                "후공정에서 이미 사용된 입고 LOT가 있어 취소할 수 없습니다: "
                + ", ".join(sorted(row[0] for row in downstream)),
            )

        db.query(ProductionLotModel).filter(ProductionLotModel.lot_no.in_(child_lots)).delete(synchronize_session=False)
        db.query(LotRelationModel).filter(
            LotRelationModel.child_lot_no.in_(child_lots),
            LotRelationModel.process_code == master.processing_type_code,
        ).delete(synchronize_session=False)

    master.status = "CANCELLED"
    master.cancelled_by = getattr(current_user, "username", None)
    master.cancelled_at = datetime.now()
    db.commit()
    db.refresh(master)
    return _serialize(master)
