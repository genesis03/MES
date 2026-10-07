"""Eligibility and explicit control-plan routing for all three inspection types."""
from fastapi import HTTPException
from models.models import ItemMasterModel, ItemBomModel, ProcessModel
from models.control_plan import ControlPlanInspectionLink
from services.standard_document_item_service import is_selectable_finished_item
from services.final_inspection_control_plan import selected_step

INTERNAL_CODES = {'LT', 'TP', 'DOT', 'ASSY'}
CATEGORIES = {'RAW_INBOUND', 'SUBCONTRACT_INBOUND', 'PROCESS', 'FINAL'}


def scope(db, document_type, category=None, process_code=None):
    if document_type == 'INBOUND':
        category = category or 'RAW_INBOUND'
        if category not in {'RAW_INBOUND', 'SUBCONTRACT_INBOUND'}:
            raise HTTPException(422, '자재 입고 또는 외주가공 입고를 선택해 주세요.')
        return category, None
    if document_type == 'FINAL':
        if category and category != 'FINAL':
            raise HTTPException(422, '최종검사 구분이 일치하지 않습니다.')
        return 'FINAL', None
    process = db.query(ProcessModel).filter(ProcessModel.process_code == process_code,
                                           ProcessModel.is_active == 'Y').first()
    if process_code not in INTERNAL_CODES or not process:
        raise HTTPException(422, 'LT·탭핑·세레이션·조립 중 등록된 내부 공정을 선택해 주세요.')
    if category and category != 'PROCESS':
        raise HTTPException(422, '공정검사 구분이 일치하지 않습니다.')
    return 'PROCESS', process_code


def eligible(db, item, category, process_code=None):
    if not item or item.is_active != 'Y':
        return False
    if category == 'RAW_INBOUND':
        return item.material_type == 'RAW'
    if category == 'SUBCONTRACT_INBOUND':
        return item.part_no.casefold().endswith('-ag')
    if category == 'FINAL':
        return is_selectable_finished_item(db, item)
    return category == 'PROCESS' and process_code in INTERNAL_CODES and item.production_loc == process_code


def require_item(db, item, category, process_code=None):
    if not eligible(db, item, category, process_code):
        raise HTTPException(422, '선택한 품번이 검사구분/등록 공정의 검색 조건에 맞지 않습니다.')


def plan_item_ids(db, item_id, category):
    # Final inspection belongs only to the finished item itself. Other types
    # follow permanent BOM IDs upwards; cycles and repeated assembly paths dedupe.
    visited = {item_id}
    frontier = {item_id}
    if category == 'FINAL':
        return visited
    while frontier:
        parents = {x[0] for x in db.query(ItemBomModel.parent_item_id).filter(
            ItemBomModel.child_item_id.in_(frontier), ItemBomModel.parent_item_id.isnot(None)).all()}
        frontier = parents - visited
        visited.update(frontier)
        if len(visited) > 2000:
            raise HTTPException(422, 'BOM 연결 범위가 너무 큽니다. BOM 관계를 확인해 주세요.')
    return visited


def allowed_steps(db, plan, category, process_code=None):
    return {x.flow_step_id for x in db.query(ControlPlanInspectionLink).filter(
        ControlPlanInspectionLink.plan_id == plan.id,
        ControlPlanInspectionLink.category == category,
        ControlPlanInspectionLink.process_code == process_code).all()}


def require_source(db, plan, item_id, category, process_code, step_id):
    if not plan or plan.item_id not in plan_item_ids(db, item_id, category):
        raise HTTPException(422, 'BOM으로 해당 품번에 연결된 관리계획서가 아닙니다.')
    owner = db.get(ItemMasterModel, plan.item_id)
    if not is_selectable_finished_item(db, owner):
        raise HTTPException(422, '사용 중인 완제품의 관리계획서를 선택해 주세요.')
    if step_id not in allowed_steps(db, plan, category, process_code):
        raise HTTPException(422, '선택한 관리계획서 공정은 이 검사구분/공정으로 연결되어 있지 않습니다.')
    return selected_step(plan, step_id)
