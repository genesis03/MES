from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.subcontract import SubcontractOrderMaster
from models.subcontract_outbound import (
    SubcontractOutboundItem,
    SubcontractOutboundLot,
    SubcontractOutboundMaster,
)

router = APIRouter(prefix="/api/subcontract/outbound", tags=["Subcontract Outbound"])


class OutboundCreateInput(BaseModel):
    order_id: int = Field(gt=0)
    outbound_date: str = Field(min_length=10, max_length=10)


def _next_outbound_no(db: Session, outbound_date: str) -> str:
    # 짧은 형식: O + YYMMDD + 3자리 순번, 예) O260914001
    yymmdd = outbound_date.replace("-", "")[2:]
    prefix = f"O{yymmdd}"
    latest = (
        db.query(SubcontractOutboundMaster.outbound_no)
        .filter(SubcontractOutboundMaster.outbound_no.like(prefix + "%"))
        .order_by(SubcontractOutboundMaster.outbound_no.desc())
        .first()
    )
    sequence = 1
    if latest:
        try:
            sequence = int(latest[0][-3:]) + 1
        except (ValueError, TypeError):
            sequence = 1
    return f"{prefix}{sequence:03d}"


def _active_outbound(db: Session, order_id: int):
    return (
        db.query(SubcontractOutboundMaster)
        .filter(
            SubcontractOutboundMaster.order_id == order_id,
            SubcontractOutboundMaster.status == "OUTBOUND",
        )
        .order_by(SubcontractOutboundMaster.id.desc())
        .first()
    )


def _serialize_source_order(order: SubcontractOrderMaster, outbound: SubcontractOutboundMaster | None = None):
    items = []
    for item in order.items:
        allocations = [
            {
                "lot_no": row.lot_no,
                "lot_qty": float(row.lot_qty or 0),
                "allocated_qty": float(row.allocated_qty or 0),
            }
            for row in item.allocations
        ]
        items.append({
            "order_item_id": item.id,
            "previous_part_no": item.previous_part_no,
            "order_part_no": item.order_part_no,
            "order_part_name": item.order_part_name,
            "spec": item.spec or "",
            "unit": item.unit,
            "outbound_qty": sum(x["allocated_qty"] for x in allocations),
            "lot_count": len(allocations),
            "lots": allocations,
            "note": item.note or "",
        })
    return {
        "order_id": order.id,
        "order_no": order.order_no,
        "order_date": order.order_date,
        "partner_id": order.partner_id,
        "partner_name": order.partner_name,
        "processing_type_code": order.processing_type_code,
        "processing_type_name": order.processing_type_name,
        "external_storage_location": order.external_storage_location,
        "manager_name": order.manager_name or "",
        "status": order.status,
        "note": order.note or "",
        "items": items,
        "outbound": _serialize_outbound(outbound) if outbound else None,
    }


def _serialize_outbound(master: SubcontractOutboundMaster | None):
    if master is None:
        return None
    return {
        "id": master.id,
        "outbound_no": master.outbound_no,
        "outbound_date": master.outbound_date,
        "order_id": master.order_id,
        "order_no": master.order_no,
        "partner_id": master.partner_id,
        "partner_name": master.partner_name,
        "processing_type_code": master.processing_type_code,
        "processing_type_name": master.processing_type_name,
        "external_storage_location": master.external_storage_location,
        "manager_name": master.manager_name or "",
        "status": master.status,
        "status_name": "출고완료" if master.status == "OUTBOUND" else "출고취소",
        "note": master.note or "",
        "items": [
            {
                "id": item.id,
                "order_item_id": item.order_item_id,
                "previous_part_no": item.previous_part_no,
                "order_part_no": item.order_part_no,
                "order_part_name": item.order_part_name,
                "spec": item.spec or "",
                "unit": item.unit,
                "outbound_qty": float(item.outbound_qty or 0),
                "lot_count": item.lot_count,
                "note": item.note or "",
                "lots": [
                    {"lot_no": lot.lot_no, "outbound_qty": float(lot.outbound_qty or 0)}
                    for lot in item.lots
                ],
            }
            for item in master.items
        ],
    }


@router.get("/orders")
def outbound_order_list(
    keyword: Optional[str] = Query(None, max_length=100),
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    query = db.query(SubcontractOrderMaster).filter(SubcontractOrderMaster.status == "ORDERED")
    if keyword:
        q = keyword.strip()
        query = query.filter(
            (SubcontractOrderMaster.order_no.contains(q, autoescape=True))
            | (SubcontractOrderMaster.partner_name.contains(q, autoescape=True))
        )
    orders = query.order_by(SubcontractOrderMaster.order_date.desc(), SubcontractOrderMaster.id.desc()).limit(limit).all()
    items = []
    for order in orders:
        active = _active_outbound(db, order.id)
        total_qty = sum(float(item.order_qty or 0) for item in order.items)
        items.append({
            "order_id": order.id,
            "order_no": order.order_no,
            "order_date": order.order_date,
            "partner_name": order.partner_name,
            "processing_type_name": order.processing_type_name,
            "item_count": len(order.items),
            "total_qty": total_qty,
            "outbound_id": active.id if active else None,
            "outbound_no": active.outbound_no if active else "",
            "outbound_date": active.outbound_date if active else "",
            "outbound_status": active.status if active else "WAITING",
            "outbound_status_name": "출고완료" if active else "출고대기",
        })
    return {"total": len(items), "items": items}


@router.get("/history")
def outbound_history_list(
    outbound_no: Optional[str] = Query(None, max_length=20),
    order_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "시작일은 종료일 이후일 수 없습니다.")

    query = db.query(SubcontractOutboundMaster).filter(SubcontractOutboundMaster.status == "OUTBOUND")
    if outbound_no:
        query = query.filter(SubcontractOutboundMaster.outbound_no.contains(outbound_no.strip(), autoescape=True))
    if order_no:
        query = query.filter(SubcontractOutboundMaster.order_no.contains(order_no.strip(), autoescape=True))
    if partner_name:
        query = query.filter(SubcontractOutboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
    if start_date:
        query = query.filter(SubcontractOutboundMaster.outbound_date >= start_date)
    if end_date:
        query = query.filter(SubcontractOutboundMaster.outbound_date <= end_date)

    rows = query.order_by(SubcontractOutboundMaster.outbound_date.desc(), SubcontractOutboundMaster.id.desc()).limit(limit).all()
    items = []
    for row in rows:
        items.append({
            "outbound_id": row.id,
            "outbound_no": row.outbound_no,
            "outbound_date": row.outbound_date,
            "order_id": row.order_id,
            "order_no": row.order_no,
            "partner_name": row.partner_name,
            "processing_type_name": row.processing_type_name,
            "item_count": len(row.items),
            "status": row.status,
            "status_name": "출고완료",
        })
    return {"total": len(items), "items": items}


@router.get("/order/{order_id}")
def get_outbound_source_order(
    order_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.get(SubcontractOrderMaster, order_id)
    if order is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")
    if order.status != "ORDERED":
        raise HTTPException(409, "발주완료 상태의 외주가공 발주만 출고할 수 있습니다.")
    return _serialize_source_order(order, _active_outbound(db, order.id))


@router.get("/{outbound_id}")
def get_outbound(
    outbound_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractOutboundMaster, outbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 출고 내역을 찾을 수 없습니다.")
    data = _serialize_outbound(master)
    order = db.get(SubcontractOrderMaster, master.order_id)
    data["order_date"] = order.order_date if order else ""
    return data


@router.post("")
def create_outbound(
    payload: OutboundCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    order = db.get(SubcontractOrderMaster, payload.order_id)
    if order is None:
        raise HTTPException(404, "외주가공 발주를 찾을 수 없습니다.")
    if order.status != "ORDERED":
        raise HTTPException(409, "발주완료 상태의 외주가공 발주만 출고할 수 있습니다.")
    if _active_outbound(db, order.id):
        raise HTTPException(409, "이미 출고완료된 외주가공 발주입니다.")
    if not order.items:
        raise HTTPException(422, "출고할 발주 품목이 없습니다.")

    for item in order.items:
        if not item.allocations:
            raise HTTPException(409, f"{item.order_part_no}의 LOT 배정이 없습니다.")
        allocated = sum(float(x.allocated_qty or 0) for x in item.allocations)
        if abs(allocated - float(item.order_qty or 0)) >= 1e-9:
            raise HTTPException(409, f"{item.order_part_no}의 LOT 배정수량이 발주수량과 일치하지 않습니다.")
        if any(abs(float(x.allocated_qty or 0) - float(x.lot_qty or 0)) >= 1e-9 for x in item.allocations):
            raise HTTPException(409, f"{item.order_part_no}의 출고 LOT는 LOT 전체수량을 사용해야 합니다.")

    master = SubcontractOutboundMaster(
        outbound_no=_next_outbound_no(db, payload.outbound_date),
        outbound_date=payload.outbound_date,
        order_id=order.id,
        order_no=order.order_no,
        partner_id=order.partner_id,
        partner_name=order.partner_name,
        processing_type_code=order.processing_type_code,
        processing_type_name=order.processing_type_name,
        external_storage_location=order.external_storage_location,
        manager_name=order.manager_name,
        status="OUTBOUND",
        note=order.note,
        created_by=getattr(current_user, "username", None),
    )
    for source in order.items:
        out_item = SubcontractOutboundItem(
            order_item_id=source.id,
            previous_part_no=source.previous_part_no,
            order_part_no=source.order_part_no,
            order_part_name=source.order_part_name,
            spec=source.spec,
            unit=source.unit,
            outbound_qty=sum(float(x.allocated_qty or 0) for x in source.allocations),
            lot_count=len(source.allocations),
            note=source.note,
        )
        for allocation in source.allocations:
            out_item.lots.append(SubcontractOutboundLot(
                allocation_id=allocation.id,
                lot_no=allocation.lot_no,
                outbound_qty=float(allocation.allocated_qty or 0),
            ))
        master.items.append(out_item)
    db.add(master)
    db.commit()
    db.refresh(master)
    return _serialize_outbound(master)


@router.post("/{outbound_id}/cancel")
def cancel_outbound(
    outbound_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    master = db.get(SubcontractOutboundMaster, outbound_id)
    if master is None:
        raise HTTPException(404, "외주가공 출고 내역을 찾을 수 없습니다.")
    if master.status == "CANCELLED":
        return _serialize_outbound(master)

    lot_nos = sorted({lot.lot_no for item in master.items for lot in item.lots if lot.lot_no})
    if lot_nos:
        downstream = (
            db.query(LotRelationModel.parent_lot_no)
            .filter(LotRelationModel.parent_lot_no.in_(lot_nos))
            .distinct()
            .all()
        )
        if downstream:
            raise HTTPException(
                409,
                "후공정에서 이미 사용된 LOT가 있어 출고를 취소할 수 없습니다: "
                + ", ".join(sorted(row[0] for row in downstream)),
            )

    master.status = "CANCELLED"
    master.cancelled_by = getattr(current_user, "username", None)
    master.cancelled_at = datetime.now()
    db.commit()
    db.refresh(master)
    return _serialize_outbound(master)
