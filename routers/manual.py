from datetime import datetime
from urllib.parse import quote
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.database import get_db
from models.models import ManualLabelModel, UserModel
from core.security import require_permission
from services.excel_service import create_manual_history_excel

router = APIRouter(prefix="/api/manual", tags=["Manual Labels"])

@router.get("/data")
async def get_manual_data(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "manual", "READ"))
):
    """수기 등록 이력 목록 조회 (읽기 권한 필요)"""
    records = db.query(ManualLabelModel).order_by(desc(ManualLabelModel.id)).all()
    rows = [
        {
            "id": r.id,
            "created_at": r.created_at,
            "barcode": r.barcode,
            "customer": r.customer,
            "delivery_date": r.delivery_date,
            "part_no": r.part_no,
            "part_name": r.part_name,
            "qty": r.qty,
            "serial": r.serial
        }
        for r in records
    ]
    return {"data": rows}

@router.post("/add")
async def add_manual_data(
    request: Request, 
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "manual", "WRITE"))
):
    """수기 입력 데이터 DB 저장 (수기발행 쓰기 권한자 전용)"""
    body = await request.json()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    customer = str(body.get("customer", "(주)유라")).strip()
    delivery_date = str(body.get("delivery_date", "")).strip()
    part_no = str(body.get("part_no", "")).strip()
    part_name = str(body.get("part_name", "")).strip()
    qty_str = str(body.get("qty", "0")).replace(",", "").strip()
    qty = int(qty_str) if qty_str.isdigit() else 0
    serial = str(body.get("serial", "")).strip()
    
    barcode = str(body.get("barcode", "")).strip()
    if not barcode:
        barcode = f"P{part_no}Q{qty}S{serial}"
        
    try:
        new_record = ManualLabelModel(
            created_at=now_str,
            barcode=barcode,
            customer=customer,
            delivery_date=delivery_date,
            part_no=part_no,
            part_name=part_name,
            qty=qty,
            serial=serial
        )
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        return {"status": "success", "id": new_record.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete")
async def delete_manual_data(
    request: Request, 
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "manual", "WRITE"))
):
    """수기 등록 건 삭제 (수기발행 쓰기 권한자 전용)"""
    body = await request.json()
    target_id = body.get("id")
    try:
        record = db.query(ManualLabelModel).filter(ManualLabelModel.id == target_id).first()
        if record:
            db.delete(record)
            db.commit()
        return {"status": "success"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/download-excel")
async def download_manual_history_excel(
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_permission("shipping", "manual", "READ"))
):
    """수기 발행 이력 엑셀 파일 다운로드 (읽기 권한 필요)"""
    records = db.query(ManualLabelModel).order_by(desc(ManualLabelModel.id)).all()
    rows = [
        {
            "created_at": r.created_at,
            "barcode": r.barcode,
            "customer": r.customer,
            "delivery_date": r.delivery_date,
            "part_no": r.part_no,
            "part_name": r.part_name,
            "qty": r.qty,
            "serial": r.serial
        }
        for r in records
    ]

    if not rows:
        return Response(content="다운로드할 수기 이력 데이터가 없습니다.", media_type="text/plain; charset=utf-8")

    output = create_manual_history_excel(rows)
    timestamp = datetime.now().strftime('%y%m%d_%H%M%S')
    ascii_name = f"manual_labels_{timestamp}.xlsx"
    korean_encoded = quote(f"수기_라벨_발행대장_{timestamp}.xlsx")

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}"; filename*=UTF-8''{korean_encoded}'}
    )