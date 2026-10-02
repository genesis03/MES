"""개정 간 분석행의 명시적 이전 ID로만 비교합니다. 과거 행을 순번으로 추정 연결하지 않습니다."""
from fastapi import HTTPException
from models.fmea import FmeaRevision
from services.fmea_service import revision_dict

HEADER_LABELS = {
    "company": "회사명", "model_year": "모델연도", "team": "상호기능팀원",
    "prepared_by": "작성자", "date_prepared": "작성일", "note": "문서 비고",
    "process_owner": "공정책임", "completion_due_date": "완료예정일", "mass_production_date": "양산적용일",
    "vehicle_model_snapshot": "적용차종", "part_no_snapshot": "당시 품번", "part_name_snapshot": "당시 품명",
    "flow_revision_code": "기준 공정흐름도 개정", "basis_revision_snapshot": "기준 도면 개정",
}
ROW_LABELS = {
    "flow_step_no": "공정번호", "flow_step_name": "공정명", "flow_sort_order": "공정순서",
    "function_text": "공정 기능", "failure_mode": "고장 형태", "effects": "잠재적 영향",
    "severity": "심각도", "classification": "특별특성", "causes": "잠재적 원인", "occurrence": "발생도",
    "prevention_controls": "공정관리 예방", "detection_controls": "공정관리 검출", "detection": "검출도", "rpn": "RPN",
    "recommended_actions": "권고 조치", "responsibility": "담당자", "target_date": "목표일",
    "actions_taken": "조치 내용", "completion_date": "완료일", "new_severity": "조치 후 심각도",
    "new_occurrence": "조치 후 발생도", "new_detection": "조치 후 검출도", "new_rpn": "조치 후 RPN",
    "action_not_applicable": "조치 해당없음", "note": "분석행 비고",
}


def _value(value):
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "예" if value else "아니오"
    return str(value)


def _stage(row):
    return " · ".join(x for x in (row.get("flow_step_no"), row.get("flow_step_name")) if x) or (
        row.get("process_name_snapshot") or "기존 미연결 공정")


def fmea_changes(db, revision):
    current = revision_dict(db, revision)
    if not revision.previous_revision_id:
        return {"previous_revision": "", "warning": "", "changes": [
            {"kind": "최초 작성", "process": "", "field": "문서", "before": "", "after": current["revision_code"]}]}
    previous = db.get(FmeaRevision, revision.previous_revision_id)
    if not previous or previous.document_id != revision.document_id:
        raise HTTPException(409, "이전 개정 연결이 올바르지 않습니다.")
    before = revision_dict(db, previous)
    changes = []
    for field, label in HEADER_LABELS.items():
        old, new = _value(before.get(field)), _value(current.get(field))
        if old != new:
            changes.append({"kind": "변경", "process": "기본정보", "field": label, "before": old, "after": new})
    result = {"previous_revision": previous.revision_code, "warning": "", "changes": changes}
    if not revision.diff_tracking:
        result["warning"] = "이 개정은 분석행의 이전 연결 기록이 없어 분석행 자동 비교를 제공하지 않습니다. 개정 사유와 각 개정의 저장 내용을 확인해 주세요."
        return result
    old_rows = {x["id"]: x for x in before["rows"]}
    linked = set()
    for row in current["rows"]:
        prior_id = row["previous_row_id"]
        if prior_id:
            if prior_id not in old_rows or prior_id in linked:
                result["warning"] = "분석행의 이전 연결이 올바르지 않아 행 비교를 중단했습니다."
                result["changes"] = [x for x in changes if x["process"] == "기본정보"]
                return result
            linked.add(prior_id)
            prior = old_rows[prior_id]
            for field, label in ROW_LABELS.items():
                old, new = _value(prior.get(field)), _value(row.get(field))
                if old != new:
                    changes.append({"kind": "변경", "process": _stage(row), "field": label, "before": old, "after": new})
        else:
            changes.append({"kind": "추가", "process": _stage(row), "field": "분석행",
                            "before": "", "after": row["failure_mode"] or "미입력"})
    for row_id, row in old_rows.items():
        if row_id not in linked:
            changes.append({"kind": "제외", "process": _stage(row), "field": "분석행",
                            "before": row["failure_mode"] or "미입력", "after": "이번 개정에서 제외 · 이전 이력 보존"})
    return result
