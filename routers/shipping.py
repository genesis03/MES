import io
import json
from datetime import datetime
from urllib.parse import quote
from typing import List
import pandas as pd
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import DATABASE_URL
from core.database import get_db
from models.models import ShippingMasterModel, UserModel
from core.security import require_permission, require_api_user
from services.excel_service import create_shipping_analysis_excel

router = APIRouter(prefix="/api", tags=["Shipping Master"])

def get_shipping_records_from_db(db: Session) -> List[dict]:
    """DB에 저장된 출고 원장 데이터를 순서대로 역직렬화하여 반환"""
    records = db.query(ShippingMasterModel).order_by(ShippingMasterModel.row_order.asc()).all()
    return [json.loads(r.row_json) for r in records]

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...), 
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "WRITE"))
):
    """출고 원장 데이터 업로드 (출고관리-출고내역 쓰기 권한자 전용)"""
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
        if DATABASE_URL:
            db.execute(text("TRUNCATE TABLE shipping_master RESTART IDENTITY;"))
        else:
            db.execute(text("DELETE FROM shipping_master;"))
            try:
                db.execute(text("DELETE FROM sqlite_sequence WHERE name='shipping_master';"))
            except Exception:
                pass
        db.commit()

        new_objects = [
            ShippingMasterModel(row_order=idx, row_json=json.dumps(row, ensure_ascii=False))
            for idx, row in enumerate(rows_data)
        ]
        db.bulk_save_objects(new_objects)
        db.commit()
        return {"status": "success", "count": len(rows_data)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"데이터 저장 실패: {str(e)}")

@router.get("/data")
@router.get("/shipping/data")
async def get_data(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "READ"))
):
    """DB에 저장된 출고 원장 데이터 조회 (읽기 권한 필요)"""
    data = get_shipping_records_from_db(db)
    return {"data": data}

@router.get("/generate-excel")
async def generate_analysis_excel(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "history", "READ"))
):
    """출고 LOT 계산 분석 엑셀 파일 생성 (읽기 권한 필요)"""
    current_data = get_shipping_records_from_db(db)

    if not current_data:
        return {"error": "데이터가 없습니다."}
    
    output = create_shipping_analysis_excel(current_data)
    timestamp = datetime.now().strftime('%y%m%d_%H%M%S')
    ascii_name = f"shipping_lot_calc_{timestamp}.xlsx"
    korean_encoded = quote(f"출고_LOT_계산_{timestamp}.xlsx")
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8''{korean_encoded}'}
    )