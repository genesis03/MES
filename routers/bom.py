# routers/bom.py 상단부
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import (
    get_current_user_optional,
    get_current_user,
    require_admin_user,
    check_permission,
    parse_user_permissions,
)
# 실제 정의된 클래스명(ItemBomModel) 임포트 및 코드 호환용 별칭 정의
from models.models import ItemMasterModel, ItemBomModel

# 코드 내부 참조 호환성 유지용 별칭
ItemBOMModel = ItemBomModel

# 절대 경로 기준 templates 디렉터리 설정
BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

# --------------------------------------------------------------------------
# 이 아래부터 기존 엔드포인트 코드가 이어집니다.
# --------------------------------------------------------------------------

def check_bom_permission(user) -> bool:
    """BOM 관리 메뉴 접근 권한 확인"""
    if not user:
        return False
    if str(getattr(user, "role", "")).upper() == "ADMIN":
        return True
    return check_permission(user, "bom", "READ")


# 1. 화면 렌더링 라우터
@router.get("/basic-info/bom", response_class=HTMLResponse)
async def bom_management_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    if not check_bom_permission(user):
        return RedirectResponse(url="/shipping", status_code=303)

    return templates.TemplateResponse(
        request=request,
        name="bom_management.html",
        context={"user": user}
    )


# 2. BOM 대상 품목 목록 조회 API (완제품 및 반제품 위주 검색)
@router.get("/api/bom/parent-items")
async def get_bom_parent_items(
    request: Request,
    keyword: Optional[str] = None,
    material_type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not check_bom_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    query = db.query(ItemMasterModel).filter(ItemMasterModel.is_active == "Y")

    if material_type:
        query = query.filter(ItemMasterModel.material_type == material_type.strip())
    if keyword:
        kw = f"%{keyword.strip()}%"
        query = query.filter((ItemMasterModel.part_no.ilike(kw)) | (ItemMasterModel.part_name.ilike(kw)))

    items = query.order_by(ItemMasterModel.part_no.asc()).all()

    data = [
        {
            "item_id": it.id,
            "part_no": it.part_no,
            "part_name": it.part_name,
            "vehicle_model": it.vehicle_model or "",
            "account_type": it.account_type,
            "material_type": it.material_type,
            "unit": it.unit,
            "weight": it.weight or 0.0,
            "production_loc": it.production_loc or ""
        }
        for it in items
    ]
    return {"status": "success", "data": data}


# 3. 특정 모품번의 직속 하위 자재 목록 조회 API (Level 1)
@router.get("/api/bom/children/{parent_part_no}")
async def get_bom_children(
    parent_part_no: str,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not check_bom_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    parent_item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == parent_part_no).first()
    if not parent_item:
        raise HTTPException(status_code=404, detail="기준 품목을 찾을 수 없습니다.")

    boms = (
        db.query(ItemBomModel, ItemMasterModel)
        .outerjoin(ItemMasterModel, ItemBomModel.child_item_id == ItemMasterModel.id)
        .filter(ItemBomModel.parent_item_id == parent_item.id)
        .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
        .all()
    )

    data = []
    for bom, item in boms:
        data.append({
            "id": bom.id,
            "parent_item_id": bom.parent_item_id,
            "child_item_id": bom.child_item_id,
            "parent_part_no": parent_item.part_no,
            "child_part_no": item.part_no if item else bom.child_part_no,
            "child_part_name": item.part_name if item else "(미등록 품목)",
            "material_type": item.material_type if item else "",
            "spec": item.spec if item else "",
            "item_weight": item.weight if item else 0.0,
            "bom_type": bom.bom_type,
            "process_code": bom.process_code or "",
            "quantity": bom.quantity,
            "unit": bom.unit,
            "loss_rate": bom.loss_rate,
            "consumption_type": bom.consumption_type,
            "sort_order": bom.sort_order,
            "note": bom.note or "",
            "created_at": bom.created_at
        })

    return {"status": "success", "parent_part_no": parent_part_no, "data": data}


# 4. 다단계 트리 전개 API (재귀 조회)
@router.get("/api/bom/tree/{root_part_no}")
async def get_bom_tree(
    root_part_no: str,
    request: Request,
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not check_bom_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    root_item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == root_part_no).first()
    if not root_item:
        raise HTTPException(status_code=404, detail="기준 품목을 찾을 수 없습니다.")

    def build_tree(item_id: int, level: int = 0, visited: set = None) -> Dict[str, Any]:
        if visited is None:
            visited = set()

        item = db.query(ItemMasterModel).filter(ItemMasterModel.id == item_id).first()
        if not item:
            return {
                "item_id": item_id,
                "part_no": "",
                "part_name": "(미등록)",
                "material_type": "",
                "unit": "EA",
                "weight": 0.0,
                "level": level,
                "children": [],
            }

        node = {
            "item_id": item.id,
            "part_no": item.part_no,
            "part_name": item.part_name,
            "material_type": item.material_type,
            "unit": item.unit,
            "weight": item.weight or 0.0,
            "level": level,
            "children": []
        }

        # 순환 참조는 품번 문자열이 아니라 영구 item_id로 판정합니다.
        if item_id in visited:
            node["part_name"] += " [순환참조 오류]"
            return node

        visited.add(item_id)

        children_boms = (
            db.query(ItemBomModel)
            .filter(ItemBomModel.parent_item_id == item_id)
            .order_by(ItemBomModel.sort_order.asc(), ItemBomModel.id.asc())
            .all()
        )

        for b in children_boms:
            if not b.child_item_id:
                continue
            child_node = build_tree(b.child_item_id, level + 1, visited.copy())
            child_node["bom_id"] = b.id
            child_node["process_code"] = b.process_code or ""
            child_node["quantity"] = b.quantity
            child_node["loss_rate"] = b.loss_rate
            child_node["consumption_type"] = b.consumption_type
            node["children"].append(child_node)

        return node

    tree_data = build_tree(root_item.id, 0)
    return {"status": "success", "data": tree_data}


# 5. 하위 자재 등록 API
@router.post("/api/bom/children/create")
async def create_bom_child(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_bom_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    body = await request.json()
    parent_part_no = str(body.get("parent_part_no", "")).strip()
    child_part_no = str(body.get("child_part_no", "")).strip()
    quantity = float(body.get("quantity") or 1.0)
    unit = str(body.get("unit", "EA")).strip()

    if not parent_part_no or not child_part_no:
        raise HTTPException(status_code=400, detail="모품번과 자품번은 필수 항목입니다.")
    if parent_part_no == child_part_no:
        raise HTTPException(status_code=400, detail="모품번과 자품번이 동일할 수 없습니다.")
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="소요량은 0보다 커야 합니다.")

    # 1) 품목 마스터 등록 여부 검증
    p_item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == parent_part_no).first()
    c_item = db.query(ItemMasterModel).filter(ItemMasterModel.part_no == child_part_no).first()
    if not p_item:
        raise HTTPException(status_code=400, detail=f"모품번 [{parent_part_no}]이 품목 마스터에 등록되어 있지 않습니다.")
    if not c_item:
        raise HTTPException(status_code=400, detail=f"자품번 [{child_part_no}]이 품목 마스터에 등록되어 있지 않습니다.")

    # 2) 중복 자재 등록 방지
    exists = db.query(ItemBomModel).filter(
        ItemBomModel.parent_item_id == p_item.id,
        ItemBomModel.child_item_id == c_item.id
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail=f"이미 등록된 하위 자재입니다. ({child_part_no})")

    new_bom = ItemBomModel(
        parent_item_id=p_item.id,
        child_item_id=c_item.id,
        parent_part_no=p_item.part_no,
        child_part_no=c_item.part_no,
        bom_type=str(body.get("bom_type", "MFG")).strip(),
        process_code=str(body.get("process_code", "")).strip() or None,
        quantity=quantity,
        unit=unit,
        loss_rate=float(body.get("loss_rate") or 0.0),
        consumption_type=str(body.get("consumption_type", "AUTO")).strip(),
        sort_order=int(body.get("sort_order") or 1),
        note=str(body.get("note", "")).strip() or None,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    db.add(new_bom)
    db.commit()

    return {"status": "success", "message": "하위 자재가 등록되었습니다."}


# 6. 하위 자재 수정 API
@router.post("/api/bom/children/update")
async def update_bom_child(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_bom_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    body = await request.json()
    bom_id = body.get("id")
    if not bom_id:
        raise HTTPException(status_code=400, detail="BOM 식별자가 누락되었습니다.")

    target = db.query(ItemBomModel).filter(ItemBomModel.id == bom_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="BOM 항목을 찾을 수 없습니다.")

    quantity = float(body.get("quantity") or target.quantity)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="소요량은 0보다 커야 합니다.")

    target.process_code = str(body.get("process_code", "")).strip() or None
    target.quantity = quantity
    target.unit = str(body.get("unit", target.unit)).strip()
    target.loss_rate = float(body.get("loss_rate", target.loss_rate))
    target.consumption_type = str(body.get("consumption_type", target.consumption_type)).strip()
    target.sort_order = int(body.get("sort_order", target.sort_order))
    target.note = str(body.get("note", "")).strip() or None

    db.commit()
    return {"status": "success", "message": "BOM 구성 정보가 수정되었습니다."}


# 7. 하위 자재 삭제 API
@router.post("/api/bom/children/delete")
async def delete_bom_child(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not check_bom_permission(user):
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    body = await request.json()
    bom_id = body.get("id")
    if not bom_id:
        raise HTTPException(status_code=400, detail="BOM 식별자가 누락되었습니다.")

    target = db.query(ItemBomModel).filter(ItemBomModel.id == bom_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="BOM 항목을 찾을 수 없습니다.")

    db.delete(target)
    db.commit()
    return {"status": "success", "message": "BOM 항목이 삭제되었습니다."}
