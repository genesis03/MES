"""Map a selected process in a saved control plan into inspection draft rows."""
import json

from fastapi import HTTPException


def plan_steps(plan):
    snapshot = json.loads(plan.flow_snapshot_json)
    return snapshot.get("steps", [])


def selected_step(plan, step_id):
    step = next((x for x in plan_steps(plan) if x["id"] == step_id), None)
    if not step:
        raise HTTPException(422, "선택한 공정이 해당 관리계획서에 없습니다.")
    return step


def inspection_rows(plan, step_id):
    selected_step(plan, step_id)
    rows = [x for x in json.loads(plan.rows_json) if x["flow_step_id"] == step_id]
    result = []
    limits = {"inspection_item_name": ("검사항목", 200), "detail_no": ("관리항목 NO", 50),
              "special_characteristic": ("특별특성", 50), "inspection_tool": ("확인방법", 200),
              "inspection_frequency": ("검사주기", 100), "sample_qty_text": ("검사수량", 100),
              "record_management": ("기록관리", 200)}
    for position, row in enumerate(rows, 1):
        names = list(dict.fromkeys(str(row.get(k) or "").strip() for k in ("product", "process")))
        name = " / ".join(x for x in names if x)
        if not name:
            raise HTTPException(422, f"관리항목 {position}행의 제품/공정 항목명이 비어 있습니다.")
        notes = []
        if row.get("reaction"):
            notes.append("이상 발생 시 조치: " + row["reaction"])
        if row.get("note"):
            notes.append(row["note"])
        item = {
            "sort_order": position, "inspection_no": str(row.get("item_no") or position), "inspection_group_no": None,
            "inspection_item_name": name, "detail_no": row.get("item_no") or None,
            "special_characteristic": row.get("classification") or None,
            "inspection_tool": row.get("method") or None,
            "spec_text": row.get("specification") or None,
            "nominal_value": None, "lower_limit": None, "upper_limit": None, "unit": None,
            "inspection_frequency": row.get("sample_frequency") or None,
            "sample_qty_text": row.get("sample_size") or None,
            "record_management": row.get("control_method") or None,
            "note": "\n".join(notes) or None,
        }
        for field, (label, limit) in limits.items():
            if len(item.get(field) or "") > limit:
                raise HTTPException(422, f"관리항목 {position}행의 {label} 내용이 기준서 허용 길이({limit}자)를 초과합니다. 내용을 확인해 주세요.")
        result.append(item)
    if not result:
        raise HTTPException(422, "선택한 공정에 불러올 관리항목이 없습니다.")
    return result
