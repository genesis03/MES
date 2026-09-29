from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import (
    CommonCodeModel,
    ItemMasterModel,
    PurchaseInboundItem,
    PurchaseInboundMaster,
    PurchaseOrderItem,
    PurchaseOrderMaster,
)
from models.quality import QualityInboundDefectDetail, QualityInboundLotDefect, QualityInboundResult
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster

router = APIRouter(prefix="/api/quality", tags=["Quality"])

INSPECTION_STATUS_NAMES = {
    "WAITING": "검사대기",
    "COMPLETED": "검사완료",
}
JUDGMENT_NAMES = {
    "PASS": "합격",
    "HOLD": "보류",
    "REJECT": "불합격",
}
SOURCE_NAMES = {
    "GENERAL": "일반구매",
    "SUBCONTRACT": "외주가공",
}


class QualityDefectItemPayload(BaseModel):
    defect_type_code: str = Field(min_length=1, max_length=30)
    defect_qty: float = Field(gt=0)


class QualityLotDefectPayload(BaseModel):
    lot_no: str = Field(min_length=1, max_length=100)
    defect_items: List[QualityDefectItemPayload] = Field(default_factory=list)


class QualityResultPayload(BaseModel):
    defect_qty: float = Field(default=0, ge=0)
    defect_type_code: Optional[str] = Field(default=None, max_length=30)
    defect_items: Optional[List[QualityDefectItemPayload]] = None
    defect_lots: Optional[List[QualityLotDefectPayload]] = None
    judgment: str = Field(max_length=20)
    remark: Optional[str] = Field(default=None, max_length=1000)


def _current_user_name(user):
    return (
        str(getattr(user, "name", "") or "").strip()
        or str(getattr(user, "username", "") or "").strip()
        or None
    )


def _normalize_kind(kind: str):
    value = (kind or "ALL").strip().upper()
    if value not in ("ALL", "GENERAL", "SUBCONTRACT"):
        raise HTTPException(422, "지원하지 않는 입고 구분입니다.")
    return value


def _quality_result_map(db: Session, source_type: str, item_ids):
    if not item_ids:
        return {}
    rows = (
        db.query(QualityInboundResult)
        .filter(
            QualityInboundResult.source_type == source_type,
            QualityInboundResult.inbound_item_id.in_(item_ids),
        )
        .all()
    )
    return {row.inbound_item_id: row for row in rows}


def _defect_type_map(db: Session):
    rows = (
        db.query(CommonCodeModel)
        .filter(
            CommonCodeModel.group_code == "DEFECT_TYPE",
            CommonCodeModel.is_active == "Y",
        )
        .order_by(CommonCodeModel.sort_order.asc(), CommonCodeModel.id.asc())
        .all()
    )
    return {row.code: row.code_name for row in rows}


def _defect_details_for_results(db: Session, result_ids):
    if not result_ids:
        return {}
    rows = (
        db.query(QualityInboundDefectDetail)
        .filter(QualityInboundDefectDetail.result_id.in_(result_ids))
        .order_by(QualityInboundDefectDetail.id.asc())
        .all()
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row.result_id, []).append(row)
    return grouped


def _lot_defects_for_results(db: Session, result_ids):
    if not result_ids:
        return {}
    rows = (
        db.query(QualityInboundLotDefect)
        .filter(QualityInboundLotDefect.result_id.in_(result_ids))
        .order_by(QualityInboundLotDefect.lot_no.asc(), QualityInboundLotDefect.id.asc())
        .all()
    )
    grouped = {}
    for row in rows:
        grouped.setdefault(row.result_id, []).append(row)
    return grouped


def _source_lots(db: Session, source_type: str, item):
    if source_type == "GENERAL":
        lot_no = (item.internal_lot_no or item.supplier_lot_no or "").strip()
        return [{"lot_no": lot_no, "lot_qty": float(item.inbound_qty or 0)}] if lot_no else []

    rows = (
        db.query(SubcontractInboundLot)
        .filter(SubcontractInboundLot.inbound_item_id == item.id)
        .order_by(SubcontractInboundLot.id.asc())
        .all()
    )
    return [
        {
            "lot_no": (row.child_lot_no or row.source_lot_no or "").strip(),
            "lot_qty": float(row.good_qty or 0),
        }
        for row in rows
        if (row.child_lot_no or row.source_lot_no or "").strip()
    ]


def _result_fields(result, fallback_status, defect_names, detail_rows=None, lot_detail_rows=None):
    inspection_status = result.inspection_status if result else (fallback_status or "WAITING")
    judgment = result.judgment if result else ""
    defect_type_code = result.defect_type_code if result else ""
    details = []
    defect_lots = []
    if result:
        for row in (detail_rows or []):
            details.append({
                "defect_type_code": row.defect_type_code,
                "defect_type_name": defect_names.get(row.defect_type_code, row.defect_type_code),
                "defect_qty": float(row.defect_qty or 0),
            })
        if not details and float(result.defect_qty or 0) > 0 and defect_type_code:
            details.append({
                "defect_type_code": defect_type_code,
                "defect_type_name": defect_names.get(defect_type_code, defect_type_code),
                "defect_qty": float(result.defect_qty or 0),
            })

        grouped_lots = {}
        for row in (lot_detail_rows or []):
            lot = grouped_lots.setdefault(row.lot_no, {
                "lot_no": row.lot_no,
                "lot_qty": float(row.lot_qty or 0),
                "defect_items": [],
                "defect_qty": 0.0,
            })
            qty = float(row.defect_qty or 0)
            lot["defect_items"].append({
                "defect_type_code": row.defect_type_code,
                "defect_type_name": defect_names.get(row.defect_type_code, row.defect_type_code),
                "defect_qty": qty,
            })
            lot["defect_qty"] += qty
        defect_lots = list(grouped_lots.values())

    summary = " / ".join(
        f"{row['defect_type_name']} {row['defect_qty']:g}" for row in details if row["defect_qty"] > 0
    )
    return {
        "inspection_status": inspection_status,
        "inspection_status_name": INSPECTION_STATUS_NAMES.get(inspection_status, inspection_status),
        "defect_qty": float(result.defect_qty or 0) if result else 0,
        "defect_type_code": defect_type_code or "",
        "defect_type_name": summary or (defect_names.get(defect_type_code, "") if defect_type_code else ""),
        "defect_items": details,
        "defect_lots": defect_lots,
        "judgment": judgment or "",
        "judgment_name": JUDGMENT_NAMES.get(judgment, judgment) if judgment else "",
        "quality_remark": result.remark or "" if result else "",
        "updated_by": result.updated_by or "" if result else "",
    }


@router.get("/inbound-defects/options")
def inbound_defect_options(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    defect_types = (
        db.query(CommonCodeModel)
        .filter(
            CommonCodeModel.group_code == "DEFECT_TYPE",
            CommonCodeModel.is_active == "Y",
        )
        .order_by(CommonCodeModel.sort_order.asc(), CommonCodeModel.id.asc())
        .all()
    )
    return {
        "defect_types": [{"code": row.code, "name": row.code_name} for row in defect_types],
        "judgments": [{"code": code, "name": name} for code, name in JUDGMENT_NAMES.items()],
    }


@router.get("/inbound-defects")
def inbound_defect_list(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    kind: str = Query("ALL", max_length=20),
    inbound_no: Optional[str] = Query(None, max_length=30),
    order_no: Optional[str] = Query(None, max_length=30),
    partner_name: Optional[str] = Query(None, max_length=100),
    part_no: Optional[str] = Query(None, max_length=80),
    limit: int = Query(1000, ge=1, le=2000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    kind = _normalize_kind(kind)
    rows = []
    defect_names = _defect_type_map(db)

    if kind in ("ALL", "GENERAL"):
        query = (
            db.query(PurchaseInboundMaster, PurchaseInboundItem, PurchaseOrderMaster.po_no, ItemMasterModel)
            .join(PurchaseInboundItem, PurchaseInboundItem.inbound_id == PurchaseInboundMaster.id)
            .outerjoin(PurchaseOrderItem, PurchaseOrderItem.id == PurchaseInboundItem.po_item_id)
            .outerjoin(PurchaseOrderMaster, PurchaseOrderMaster.id == PurchaseOrderItem.po_id)
            .join(ItemMasterModel, ItemMasterModel.id == PurchaseInboundItem.item_id)
            .filter(PurchaseInboundMaster.status == "CONFIRMED")
        )
        if start_date:
            query = query.filter(PurchaseInboundMaster.inbound_date >= start_date)
        if end_date:
            query = query.filter(PurchaseInboundMaster.inbound_date <= end_date)
        if inbound_no:
            query = query.filter(PurchaseInboundMaster.inbound_no.contains(inbound_no.strip(), autoescape=True))
        if order_no:
            query = query.filter(PurchaseOrderMaster.po_no.contains(order_no.strip(), autoescape=True))
        if partner_name:
            query = query.filter(PurchaseInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
        if part_no:
            query = query.filter(PurchaseInboundItem.part_no.contains(part_no.strip(), autoescape=True))

        general_rows = (
            query.order_by(PurchaseInboundMaster.inbound_date.desc(), PurchaseInboundMaster.id.desc(), PurchaseInboundItem.id)
            .limit(limit)
            .all()
        )
        result_map = _quality_result_map(db, "GENERAL", [item.id for _, item, _, _ in general_rows])
        result_ids = [row.id for row in result_map.values()]
        detail_map = _defect_details_for_results(db, result_ids)
        lot_detail_map = _lot_defects_for_results(db, result_ids)
        for master, item, po_no, product in general_rows:
            data = {
                "source_type": "GENERAL",
                "source_name": SOURCE_NAMES["GENERAL"],
                "inbound_id": master.id,
                "inbound_item_id": item.id,
                "inbound_date": master.inbound_date,
                "inbound_no": master.inbound_no,
                "order_no": po_no or "",
                "partner_name": master.partner_name,
                "item_id": item.item_id,
                "part_no": product.part_no,
                "part_name": product.part_name,
                "inbound_qty": float(item.inbound_qty or 0),
                "unit": item.unit,
                "note": item.note or master.note or "",
                "lots": _source_lots(db, "GENERAL", item),
            }
            result = result_map.get(item.id)
            data.update(_result_fields(
                result,
                item.inspection_status,
                defect_names,
                detail_map.get(result.id, []) if result else [],
                lot_detail_map.get(result.id, []) if result else [],
            ))
            rows.append(data)

    if kind in ("ALL", "SUBCONTRACT"):
        query = (
            db.query(SubcontractInboundMaster, SubcontractInboundItem)
            .join(SubcontractInboundItem, SubcontractInboundItem.inbound_id == SubcontractInboundMaster.id)
            .filter(SubcontractInboundMaster.status == "RECEIVED")
        )
        if start_date:
            query = query.filter(SubcontractInboundMaster.inbound_date >= start_date)
        if end_date:
            query = query.filter(SubcontractInboundMaster.inbound_date <= end_date)
        if inbound_no:
            query = query.filter(SubcontractInboundMaster.inbound_no.contains(inbound_no.strip(), autoescape=True))
        if order_no:
            query = query.filter(SubcontractInboundMaster.order_no.contains(order_no.strip(), autoescape=True))
        if partner_name:
            query = query.filter(SubcontractInboundMaster.partner_name.contains(partner_name.strip(), autoescape=True))
        if part_no:
            query = query.filter(SubcontractInboundItem.part_no.contains(part_no.strip(), autoescape=True))

        subcontract_rows = (
            query.order_by(SubcontractInboundMaster.inbound_date.desc(), SubcontractInboundMaster.id.desc(), SubcontractInboundItem.id)
            .limit(limit)
            .all()
        )
        result_map = _quality_result_map(db, "SUBCONTRACT", [item.id for _, item in subcontract_rows])
        result_ids = [row.id for row in result_map.values()]
        detail_map = _defect_details_for_results(db, result_ids)
        lot_detail_map = _lot_defects_for_results(db, result_ids)
        for master, item in subcontract_rows:
            data = {
                "source_type": "SUBCONTRACT",
                "source_name": SOURCE_NAMES["SUBCONTRACT"],
                "inbound_id": master.id,
                "inbound_item_id": item.id,
                "inbound_date": master.inbound_date,
                "inbound_no": master.inbound_no,
                "order_no": master.order_no,
                "partner_name": master.partner_name,
                "item_id": item.item_id,
                "part_no": item.part_no,
                "part_name": item.part_name,
                "inbound_qty": float(item.good_qty or 0),
                "unit": item.unit,
                "note": item.note or master.note or "",
                "lots": _source_lots(db, "SUBCONTRACT", item),
            }
            result = result_map.get(item.id)
            data.update(_result_fields(
                result,
                "WAITING",
                defect_names,
                detail_map.get(result.id, []) if result else [],
                lot_detail_map.get(result.id, []) if result else [],
            ))
            rows.append(data)

    rows.sort(key=lambda row: (row["inbound_date"], row["inbound_no"], row["inbound_item_id"]), reverse=True)
    return {"total": len(rows), "items": rows[:limit]}


def _get_source_item(db: Session, source_type: str, inbound_item_id: int):
    if source_type == "GENERAL":
        item = db.get(PurchaseInboundItem, inbound_item_id)
        if not item:
            raise HTTPException(404, "일반구매 입고 품목을 찾을 수 없습니다.")
        master = db.get(PurchaseInboundMaster, item.inbound_id)
        if not master or master.status != "CONFIRMED":
            raise HTTPException(409, "입고확정 상태의 일반구매 품목만 품질 처리할 수 있습니다.")
        return master, item, float(item.inbound_qty or 0)

    if source_type == "SUBCONTRACT":
        item = db.get(SubcontractInboundItem, inbound_item_id)
        if not item:
            raise HTTPException(404, "외주가공 입고 품목을 찾을 수 없습니다.")
        master = db.get(SubcontractInboundMaster, item.inbound_id)
        if not master or master.status != "RECEIVED":
            raise HTTPException(409, "입고완료 상태의 외주가공 품목만 품질 처리할 수 있습니다.")
        return master, item, float(item.good_qty or 0)

    raise HTTPException(422, "지원하지 않는 입고 구분입니다.")


@router.post("/inbound-defects/{source_type}/{inbound_item_id}")
def save_inbound_defect_result(
    source_type: str,
    inbound_item_id: int,
    payload: QualityResultPayload,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    source_type = source_type.strip().upper()
    master, item, inbound_qty = _get_source_item(db, source_type, inbound_item_id)

    judgment = payload.judgment.strip().upper()
    if judgment not in JUDGMENT_NAMES:
        raise HTTPException(422, "지원하지 않는 판정값입니다.")

    source_lots = _source_lots(db, source_type, item)
    lot_qty_map = {row["lot_no"]: float(row["lot_qty"] or 0) for row in source_lots}

    defect_lots = []
    aggregate = {}
    if payload.defect_lots is not None:
        seen_lots = set()
        for lot_payload in payload.defect_lots:
            lot_no = lot_payload.lot_no.strip()
            if lot_no not in lot_qty_map:
                raise HTTPException(422, f"해당 입고품목에 없는 LOT입니다: {lot_no}")
            if lot_no in seen_lots:
                raise HTTPException(422, f"LOT가 중복 입력되었습니다: {lot_no}")
            seen_lots.add(lot_no)

            merged = {}
            for row in lot_payload.defect_items:
                code = row.defect_type_code.strip()
                merged[code] = merged.get(code, 0.0) + float(row.defect_qty)
            items = [{"code": code, "qty": qty} for code, qty in merged.items() if qty > 0]
            lot_defect_qty = sum(row["qty"] for row in items)
            if lot_defect_qty > lot_qty_map[lot_no] + 1e-9:
                raise HTTPException(422, f"{lot_no} 불량합계가 LOT 수량을 초과합니다.")
            if items:
                defect_lots.append({
                    "lot_no": lot_no,
                    "lot_qty": lot_qty_map[lot_no],
                    "items": items,
                    "defect_qty": lot_defect_qty,
                })
                for row in items:
                    aggregate[row["code"]] = aggregate.get(row["code"], 0.0) + row["qty"]

        defect_items = [{"code": code, "qty": qty} for code, qty in aggregate.items() if qty > 0]
        defect_qty = sum(row["qty"] for row in defect_items)
    else:
        detail_payload = payload.defect_items or []
        if detail_payload:
            merged = {}
            for row in detail_payload:
                code = row.defect_type_code.strip()
                merged[code] = merged.get(code, 0.0) + float(row.defect_qty)
            defect_items = [{"code": code, "qty": qty} for code, qty in merged.items() if qty > 0]
            defect_qty = sum(row["qty"] for row in defect_items)
        else:
            defect_qty = float(payload.defect_qty or 0)
            code = (payload.defect_type_code or "").strip()
            defect_items = [{"code": code, "qty": defect_qty}] if defect_qty > 0 and code else []

    if defect_qty > inbound_qty + 1e-9:
        raise HTTPException(422, "불량수량 합계는 입고수량을 초과할 수 없습니다.")
    if defect_qty > 0 and not defect_items:
        raise HTTPException(422, "불량수량이 있으면 불량유형을 입력해야 합니다.")

    valid_codes = {
        row.code for row in db.query(CommonCodeModel).filter(
            CommonCodeModel.group_code == "DEFECT_TYPE",
            CommonCodeModel.is_active == "Y",
        ).all()
    }
    invalid = [row["code"] for row in defect_items if row["code"] not in valid_codes]
    if invalid:
        raise HTTPException(422, "등록되지 않았거나 사용 중지된 불량유형입니다: " + ", ".join(invalid))

    result = (
        db.query(QualityInboundResult)
        .filter(
            QualityInboundResult.source_type == source_type,
            QualityInboundResult.inbound_item_id == inbound_item_id,
        )
        .first()
    )
    if result is None:
        result = QualityInboundResult(
            source_type=source_type,
            inbound_id=master.id,
            inbound_item_id=inbound_item_id,
        )
        db.add(result)
        db.flush()

    result.inbound_id = master.id
    result.inspection_status = "COMPLETED"
    result.defect_qty = float(defect_qty)
    result.defect_type_code = defect_items[0]["code"] if len(defect_items) == 1 else None
    result.judgment = judgment
    result.remark = (payload.remark or "").strip() or None
    result.updated_by = _current_user_name(current_user)

    db.query(QualityInboundDefectDetail).filter(
        QualityInboundDefectDetail.result_id == result.id
    ).delete(synchronize_session=False)
    for row in defect_items:
        db.add(QualityInboundDefectDetail(
            result_id=result.id,
            source_type=source_type,
            inbound_item_id=inbound_item_id,
            defect_type_code=row["code"],
            defect_qty=float(row["qty"]),
        ))

    db.query(QualityInboundLotDefect).filter(
        QualityInboundLotDefect.result_id == result.id
    ).delete(synchronize_session=False)
    for lot in defect_lots:
        for row in lot["items"]:
            db.add(QualityInboundLotDefect(
                result_id=result.id,
                source_type=source_type,
                inbound_item_id=inbound_item_id,
                lot_no=lot["lot_no"],
                lot_qty=float(lot["lot_qty"]),
                defect_type_code=row["code"],
                defect_qty=float(row["qty"]),
            ))

    if source_type == "GENERAL":
        item.inspection_status = "COMPLETED"

    db.commit()
    db.refresh(result)
    return {
        "message": "입고 품질 결과를 저장했습니다.",
        "inspection_status": result.inspection_status,
        "inspection_status_name": INSPECTION_STATUS_NAMES[result.inspection_status],
        "defect_qty": result.defect_qty,
        "judgment": result.judgment,
        "judgment_name": JUDGMENT_NAMES[result.judgment],
    }
