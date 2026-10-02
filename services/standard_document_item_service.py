"""완제품 전용 표준문서의 품목 선택/저장 공통 정책."""
from fastapi import HTTPException
from sqlalchemy import select
from models.models import CommonCodeModel, ItemMasterModel

# 기존 MATERIAL_TYPE 공통코드 및 BOM/수주에서 사용 중인 FINISHED 코드를 재사용합니다.
FINISHED_MATERIAL_TYPE = "FINISHED"


def finished_item_condition():
    codes = select(CommonCodeModel.code).where(
        CommonCodeModel.group_code == "MATERIAL_TYPE",
        CommonCodeModel.code == FINISHED_MATERIAL_TYPE,
        CommonCodeModel.is_active == "Y",
    )
    return ItemMasterModel.material_type.in_(codes)


def selectable_finished_items(db):
    return db.scalars(select(ItemMasterModel).where(
        ItemMasterModel.is_active == "Y", finished_item_condition()
    ).order_by(ItemMasterModel.part_no)).all()


def is_selectable_finished_item(db, item):
    if not item or item.is_active != "Y" or item.material_type != FINISHED_MATERIAL_TYPE:
        return False
    return db.scalar(select(CommonCodeModel.id).where(
        CommonCodeModel.group_code == "MATERIAL_TYPE",
        CommonCodeModel.code == item.material_type,
        CommonCodeModel.is_active == "Y",
    ).limit(1)) is not None


def require_finished_item(db, item):
    if not is_selectable_finished_item(db, item):
        raise HTTPException(422, "이 문서는 사용 중인 완제품 품목만 등록·수정·개정·적용할 수 있습니다. 기존 이력은 조회하거나 폐기할 수 있습니다.")
