import io
import json
from datetime import datetime
from urllib.parse import quote
from typing import List

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import DATABASE_URL
from core.database import get_db
from core.security import require_permission
from models.models import ShippingMasterModel, UserModel
from models.sales import ShipmentMaster
from services.excel_service import create_shipping_analysis_excel
from services.shipping_analysis_adapter import LEGACY_COLUMNS, build_shipping_analysis_rows

router = APIRouter(prefix="/api", tags=["Shipping Master"])


class ShipmentSelectionInput(BaseModel):
    shipment_ids: list[int] = Field(min_length=1)


def get_shipping_records_from_db(db: Session) -> List[dict]:
    records = db.query(ShippingMasterModel).order_by(ShippingMasterModel.row_order.asc()).all()
    return [json.loads(r.row_json) for r in records]


def replace_shipping_records(db: Session, rows_data: list[dict]) -> None:
    """공용 출고 원장 스테이징을 교체합니다.

    기존 CSV 업로드와 MES 출고 선택이 동일한 shipping_master를 사용하므로
    바코드 출력/검증 등 하위 기존 기능은 변경하지 않고 같은 데이터를 계속 읽습니다.
    """
    if DATABASE_URL:
        db.execute(text("TRUNCATE TABLE shipping_master RESTART IDENTITY;"))
    else:
        db.execute(text("DELETE FROM shipping_master;"))
        try:
            db.execute(text("DELETE FROM sqlite_sequence WHERE name='shipping_master';"))
        except Exception:
            pass

    new_objects = [
        ShippingMasterModel(row_order=idx, row_json=json.dumps(row, ensure_ascii=False))
        for idx, row in enumerate(rows_data)
    ]
    if new_objects:
        db.bulk_save_objects(new_objects)


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "WRITE")),
):
    contents = await file.read()
    filename = file.filename.lower()

    if filename.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(contents), dtype=str, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(contents), dtype=str, encoding="cp949")
    else:
        df = pd.read_excel(io.BytesIO(contents), dtype=str)

    df = df.fillna("")
    rows_data = df.to_dict(orient="records")

    try:
        replace_shipping_records(db, rows_data)
        db.commit()
        return {"status": "success", "count": len(rows_data)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"데이터 저장 실패: {str(e)}")


@router.get("/data")
@router.get("/shipping/data")
async def get_data(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "READ")),
):
    return {"data": get_shipping_records_from_db(db)}


def _shipment_item_box_count(item) -> int:
    if item.boxes:
        return len(item.boxes)
    direct_lots = {
        str(getattr(row, "outbound_lot_no", None) or "").strip()
        for row in getattr(item, "direct_lots", []) or []
        if str(getattr(row, "outbound_lot_no", None) or "").strip()
    }
    return len(direct_lots)


@router.get("/shipping/source-shipments")
def source_shipments(
    shipment_no: str | None = None,
    customer_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "READ")),
):
    query = db.query(ShipmentMaster).filter(ShipmentMaster.status == "CONFIRMED")
    if shipment_no and shipment_no.strip():
        query = query.filter(ShipmentMaster.shipment_no.contains(shipment_no.strip(), autoescape=True))
    if customer_name and customer_name.strip():
        query = query.filter(ShipmentMaster.customer_name.contains(customer_name.strip(), autoescape=True))
    if start_date:
        query = query.filter(ShipmentMaster.shipment_date >= start_date)
    if end_date:
        query = query.filter(ShipmentMaster.shipment_date <= end_date)

    rows = query.order_by(ShipmentMaster.shipment_date.desc(), ShipmentMaster.id.desc()).limit(1000).all()
    return {
        "items": [
            {
                "id": row.id,
                "shipment_no": row.shipment_no,
                "shipment_date": row.shipment_date,
                "customer_name": row.customer_name,
                "status": row.status,
                "item_count": len(row.items),
                "box_count": sum(_shipment_item_box_count(item) for item in row.items),
                "total_qty": sum(float(item.shipped_qty or 0) for item in row.items),
                "part_nos": [item.part_no for item in row.items],
            }
            for row in rows
        ]
    }


@router.post("/shipping/from-shipments")
def shipping_rows_from_shipments(
    payload: ShipmentSelectionInput,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "WRITE")),
):
    data = build_shipping_analysis_rows(db, payload.shipment_ids)
    if not data:
        raise HTTPException(404, "선택한 출고건에서 변환할 출고 LOT 데이터가 없습니다.")

    try:
        replace_shipping_records(db, data)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"출고 원장 저장 실패: {str(e)}")

    return {
        "columns": LEGACY_COLUMNS,
        "data": data,
        "count": len(data),
        "persisted": True,
    }


@router.post("/shipping/generate-excel-from-shipments")
def generate_analysis_excel_from_shipments(
    payload: ShipmentSelectionInput,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "READ")),
):
    current_data = build_shipping_analysis_rows(db, payload.shipment_ids)
    if not current_data:
        raise HTTPException(404, "선택한 출고건에서 변환할 데이터가 없습니다.")

    output = create_shipping_analysis_excel(current_data)
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    ascii_name = f"shipping_lot_calc_{timestamp}.xlsx"
    korean_encoded = quote(f"출고_LOT_계산_{timestamp}.xlsx")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{korean_encoded}"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )


@router.get("/generate-excel")
async def generate_analysis_excel(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "READ")),
):
    current_data = get_shipping_records_from_db(db)
    if not current_data:
        return {"error": "데이터가 없습니다."}

    output = create_shipping_analysis_excel(current_data)
    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
    ascii_name = f"shipping_lot_calc_{timestamp}.xlsx"
    korean_encoded = quote(f"출고_LOT_계산_{timestamp}.xlsx")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{korean_encoded}"
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": disposition},
    )
