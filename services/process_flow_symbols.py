"""공정 기호 선택은 공통코드를 사용하고 개정별 명칭/도형 스냅샷을 보존합니다."""
import json
from fastapi import HTTPException
from sqlalchemy import select
from models.models import CommonCodeModel

SYMBOL_GROUP = "PROCESS_FLOW_SYMBOL"
# 아래 값은 업무 마스터가 아니라 SVG 렌더러가 지원하는 도형 식별자입니다.
SUPPORTED_SHAPES = frozenset({"CIRCLE", "ARROW", "SQUARE", "DIAMOND", "INVERTED_TRIANGLE", "DELAY", "DIAMOND_SQUARE", "SQUARE_DIAMOND", "CIRCLE_SQUARE", "CIRCLE_ARROW"})


def _symbol(row):
    try:
        shape = json.loads(row.note or "{}").get("shape")
    except (ValueError, AttributeError):
        shape = None
    if not isinstance(shape, str) or shape not in SUPPORTED_SHAPES:
        raise HTTPException(409, f"공정 기호 공통코드 {row.code}의 도형 설정을 확인해 주세요.")
    return {"code": row.code, "name": row.code_name, "shape": shape}


def flow_symbol_options(db):
    rows = db.scalars(select(CommonCodeModel).where(
        CommonCodeModel.group_code == SYMBOL_GROUP, CommonCodeModel.is_active == "Y"
    ).order_by(CommonCodeModel.sort_order, CommonCodeModel.id)).all()
    if len({row.code for row in rows}) != len(rows):
        raise HTTPException(409, "공정 기호 공통코드가 중복되어 있습니다. 공통코드 관리에서 확인해 주세요.")
    return [_symbol(row) for row in rows]


def resolve_flow_symbol(db, code):
    rows = db.scalars(select(CommonCodeModel).where(
        CommonCodeModel.group_code == SYMBOL_GROUP, CommonCodeModel.code == code,
        CommonCodeModel.is_active == "Y"
    )).all()
    if len(rows) != 1:
        raise HTTPException(422, "사용 중인 공정 기호를 선택해 주세요. 공통코드 중복/사용 여부를 확인해 주세요.")
    return _symbol(rows[0])
