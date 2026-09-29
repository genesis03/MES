from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import CommonCodeModel, ItemMasterModel, ProcessModel, PurchaseInboundItem, PurchaseInboundMaster
from models.production import ProductionWorkOrder
from models.production_defect import QualityProductionDefect, QualityProductionDefectDetail
from models.production_lot import ProductionLotModel
from models.production_run import ProductionRun, ProductionRunDefect
from models.quality import QualityInboundDefectDetail, QualityInboundResult
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundMaster

router = APIRouter(prefix="/api/quality/defect-status", tags=["Quality Defect Status"])


def _defect_name_map(db: Session) -> dict[str, str]:
    rows = (
        db.query(CommonCodeModel)
        .filter(CommonCodeModel.group_code == "DEFECT_TYPE")
        .order_by(CommonCodeModel.sort_order.asc(), CommonCodeModel.id.asc())
        .all()
    )
    return {row.code: row.code_name for row in rows}


def _process_name_map(db: Session) -> dict[str, str]:
    return {
        row.process_code: row.process_name
        for row in db.query(ProcessModel).all()
        if row.process_code
    }


def _matches(row: dict, part_no: str, defect_type_code: str, source: str) -> bool:
    if part_no and part_no.lower() not in str(row.get("part_no") or "").lower():
        return False
    if defect_type_code and defect_type_code not in row.get("defect_codes", []):
        return False
    if source != "ALL" and row.get("source_type") != source:
        return False
    return True


@router.get("")
def defect_status(
    start_date: Optional[str] = Query(None, max_length=10),
    end_date: Optional[str] = Query(None, max_length=10),
    source: str = Query("ALL", max_length=30),
    part_no: Optional[str] = Query(None, max_length=80),
    defect_type_code: Optional[str] = Query(None, max_length=30),
    limit: int = Query(2000, ge=1, le=5000),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    source = (source or "ALL").strip().upper()
    allowed_sources = {"ALL", "GENERAL", "SUBCONTRACT", "PRODUCTION_RUN", "PRODUCTION_LOT"}
    if source not in allowed_sources:
        source = "ALL"
    part_filter = (part_no or "").strip()
    defect_filter = (defect_type_code or "").strip()
    names = _defect_name_map(db)
    process_names = _process_name_map(db)
    rows: list[dict] = []

    # 일반구매/외주 입고 불량
    inbound_results = (
        db.query(QualityInboundResult)
        .filter(QualityInboundResult.inspection_status == "COMPLETED", QualityInboundResult.defect_qty > 0)
        .order_by(QualityInboundResult.updated_at.desc(), QualityInboundResult.id.desc())
        .all()
    )
    inbound_result_ids = [row.id for row in inbound_results]
    inbound_details = (
        db.query(QualityInboundDefectDetail)
        .filter(QualityInboundDefectDetail.result_id.in_(inbound_result_ids))
        .order_by(QualityInboundDefectDetail.id.asc())
        .all()
        if inbound_result_ids else []
    )
    inbound_detail_map: dict[int, list] = {}
    for detail in inbound_details:
        inbound_detail_map.setdefault(detail.result_id, []).append(detail)

    for result in inbound_results:
        details = inbound_detail_map.get(result.id, [])
        defect_items = [
            {
                "code": detail.defect_type_code,
                "name": names.get(detail.defect_type_code, detail.defect_type_code),
                "qty": float(detail.defect_qty or 0),
            }
            for detail in details
            if float(detail.defect_qty or 0) > 0
        ]
        if not defect_items and result.defect_type_code and float(result.defect_qty or 0) > 0:
            defect_items = [{
                "code": result.defect_type_code,
                "name": names.get(result.defect_type_code, result.defect_type_code),
                "qty": float(result.defect_qty or 0),
            }]

        if result.source_type == "GENERAL":
            item = db.get(PurchaseInboundItem, result.inbound_item_id)
            master = db.get(PurchaseInboundMaster, result.inbound_id)
            product = db.get(ItemMasterModel, item.item_id) if item and item.item_id else None
            if not item or not master:
                continue
            date_value = master.inbound_date
            row = {
                "source_type": "GENERAL",
                "source_name": "입고-일반구매",
                "date": date_value,
                "part_no": product.part_no if product else item.part_no,
                "part_name": product.part_name if product else "",
                "process_name": "",
                "lot_no": item.internal_lot_no or item.supplier_lot_no or "",
                "base_qty": float(item.inbound_qty or 0),
                "defect_qty": float(result.defect_qty or 0),
                "defect_items": defect_items,
                "defect_codes": [x["code"] for x in defect_items],
                "partner_name": master.partner_name or "",
                "created_by": result.updated_by or "",
                "target_key": f"GENERAL:{item.id}",
            }
        elif result.source_type == "SUBCONTRACT":
            item = db.get(SubcontractInboundItem, result.inbound_item_id)
            master = db.get(SubcontractInboundMaster, result.inbound_id)
            product = db.get(ItemMasterModel, item.item_id) if item and item.item_id else None
            if not item or not master:
                continue
            date_value = master.inbound_date
            row = {
                "source_type": "SUBCONTRACT",
                "source_name": "입고-외주가공",
                "date": date_value,
                "part_no": product.part_no if product else item.part_no,
                "part_name": product.part_name if product else item.part_name or "",
                "process_name": getattr(master, "processing_type_name", "") or "",
                "lot_no": "",
                "base_qty": float(item.good_qty or 0),
                "defect_qty": float(result.defect_qty or 0),
                "defect_items": defect_items,
                "defect_codes": [x["code"] for x in defect_items],
                "partner_name": master.partner_name or "",
                "created_by": result.updated_by or "",
                "target_key": f"SUBCONTRACT:{item.id}",
            }
        else:
            continue

        if start_date and row["date"] < start_date:
            continue
        if end_date and row["date"] > end_date:
            continue
        if _matches(row, part_filter, defect_filter, source):
            rows.append(row)

    # 생산실적 입력 시 발생한 공정 불량
    run_query = db.query(ProductionRun).filter(
        ProductionRun.status == "COMPLETED",
        ProductionRun.defect_qty > 0,
    )
    if start_date:
        run_query = run_query.filter(ProductionRun.performance_date >= start_date)
    if end_date:
        run_query = run_query.filter(ProductionRun.performance_date <= end_date)
    for run in run_query.order_by(ProductionRun.performance_date.desc(), ProductionRun.id.desc()).all():
        work_order = db.get(ProductionWorkOrder, run.work_order_id)
        product = db.get(ItemMasterModel, work_order.item_id) if work_order and work_order.item_id else None
        details = (
            db.query(ProductionRunDefect)
            .filter(ProductionRunDefect.run_id == run.id, ProductionRunDefect.defect_qty > 0)
            .order_by(ProductionRunDefect.id.asc())
            .all()
        )
        defect_items = [{
            "code": detail.defect_type_code,
            "name": detail.defect_type_name or names.get(detail.defect_type_code, detail.defect_type_code),
            "qty": float(detail.defect_qty or 0),
        } for detail in details]
        row = {
            "source_type": "PRODUCTION_RUN",
            "source_name": "생산실적",
            "date": run.performance_date,
            "part_no": product.part_no if product else (work_order.part_no if work_order else ""),
            "part_name": product.part_name if product else "",
            "process_name": process_names.get(run.process_code, run.process_code),
            "lot_no": "",
            "base_qty": float(run.good_qty or 0) + float(run.defect_qty or 0),
            "defect_qty": float(run.defect_qty or 0),
            "defect_items": defect_items,
            "defect_codes": [x["code"] for x in defect_items],
            "partner_name": "",
            "created_by": run.created_by or run.operator_name or "",
            "target_key": f"PRODUCTION_RUN:{run.id}",
        }
        if _matches(row, part_filter, defect_filter, source):
            rows.append(row)

    # 생산 완료 LOT에서 후검출된 불량
    lot_defects = (
        db.query(QualityProductionDefect)
        .filter(QualityProductionDefect.status == "ACTIVE", QualityProductionDefect.defect_qty > 0)
        .order_by(QualityProductionDefect.defect_date.desc(), QualityProductionDefect.id.desc())
        .all()
    )
    defect_ids = [row.id for row in lot_defects]
    detail_rows = (
        db.query(QualityProductionDefectDetail)
        .filter(QualityProductionDefectDetail.defect_id.in_(defect_ids))
        .order_by(QualityProductionDefectDetail.id.asc())
        .all()
        if defect_ids else []
    )
    lot_detail_map: dict[int, list] = {}
    for detail in detail_rows:
        lot_detail_map.setdefault(detail.defect_id, []).append(detail)

    for defect in lot_defects:
        if start_date and defect.defect_date < start_date:
            continue
        if end_date and defect.defect_date > end_date:
            continue
        product = db.get(ItemMasterModel, defect.item_id)
        lot = db.get(ProductionLotModel, defect.production_lot_id)
        details = lot_detail_map.get(defect.id, [])
        defect_items = [{
            "code": detail.defect_type_code,
            "name": names.get(detail.defect_type_code, detail.defect_type_code),
            "qty": float(detail.defect_qty or 0),
        } for detail in details if float(detail.defect_qty or 0) > 0]
        row = {
            "source_type": "PRODUCTION_LOT",
            "source_name": "생산 LOT",
            "date": defect.defect_date,
            "part_no": product.part_no if product else (lot.part_no if lot else ""),
            "part_name": product.part_name if product else "",
            "process_name": "",
            "lot_no": defect.lot_no,
            "base_qty": float(defect.lot_qty or 0),
            "defect_qty": float(defect.defect_qty or 0),
            "defect_items": defect_items,
            "defect_codes": [x["code"] for x in defect_items],
            "partner_name": "",
            "created_by": defect.created_by or "",
            "target_key": f"PRODUCTION_LOT:{defect.production_lot_id}",
        }
        if _matches(row, part_filter, defect_filter, source):
            rows.append(row)

    rows.sort(key=lambda x: (x["date"], x["source_name"], x["part_no"]), reverse=True)
    rows = rows[:limit]
    total_defect = sum(float(row["defect_qty"] or 0) for row in rows)

    return {
        "items": rows,
        "total": len(rows),
        "total_defect_qty": total_defect,
        "defect_types": [{"code": code, "name": name} for code, name in names.items()],
    }
