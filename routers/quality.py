from typing import Optional

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
from models.quality import QualityInboundResult
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundMaster

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


class QualityResultPayload(BaseModel):
    defect_qty: float = Field(default=0, ge=0)
    defect_type_code: Optional[str] = Field(default=None, max_length=30)
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


def _result_fields(result, fallback_status, defect_names):
    inspection_status = result.inspection_status if result else (fallback_status or "WAITING")
    judgment = result.judgment if result else ""
    defect_type_code = result.defect_type_code if result else ""
    return {
        "inspection_status": inspection_status,
        "inspection_status_name": INSPECTION_STATUS_NAMES.get(inspection_status, inspection_status),
        "defect_qty": float(result.defect_qty or 0) if result else 0,
        "defect_type_code": defect_type_code or "",
        "defect_type_name": defect_names.get(defect_type_code, "") if defect_type_code else "",
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
            .join(ItemMasterModel, ItemMasterModel.part_no == PurchaseInboundItem.part_no)
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
                "part_no": item.part_no,
                "part_name": product.part_name,
                "inbound_qty": float(item.inbound_qty or 0),
                "unit": item.unit,
                "note": item.note or master.note or "",
            }
            data.update(_result_fields(result_map.get(item.id), item.inspection_status, defect_names))
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
                "part_no": item.part_no,
                "part_name": item.part_name,
                "inbound_qty": float(item.good_qty or 0),
                "unit": item.unit,
                "note": item.note or master.note or "",
            }
            data.update(_result_fields(result_map.get(item.id), "WAITING", defect_names))
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
    if payload.defect_qty > inbound_qty:
        raise HTTPException(422, "불량수량은 입고수량을 초과할 수 없습니다.")

    defect_type_code = (payload.defect_type_code or "").strip() or None
    if payload.defect_qty > 0 and not defect_type_code:
        raise HTTPException(422, "불량수량이 있으면 불량유형을 선택해야 합니다.")
    if defect_type_code:
        valid_code = (
            db.query(CommonCodeModel)
            .filter(
                CommonCodeModel.group_code == "DEFECT_TYPE",
                CommonCodeModel.code == defect_type_code,
                CommonCodeModel.is_active == "Y",
            )
            .first()
        )
        if not valid_code:
            raise HTTPException(422, "등록되지 않았거나 사용 중지된 불량유형입니다.")

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

    result.inbound_id = master.id
    result.inspection_status = "COMPLETED"
    result.defect_qty = float(payload.defect_qty)
    result.defect_type_code = defect_type_code
    result.judgment = judgment
    result.remark = (payload.remark or "").strip() or None
    result.updated_by = _current_user_name(current_user)

    if source_type == "GENERAL":
        item.inspection_status = "COMPLETED"

    db.commit()
    db.refresh(result)
    return {
        "message": "입고 품질 결과를 저장했습니다.",
        "inspection_status": result.inspection_status,
        "inspection_status_name": INSPECTION_STATUS_NAMES[result.inspection_status],
        "judgment": result.judgment,
        "judgment_name": JUDGMENT_NAMES[result.judgment],
    }
