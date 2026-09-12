"""Register the agreed MES reference data in the configured database.

Run from the repository root: python scripts/seed_reference_data.py
Existing rows are matched by code and corrected to the agreed names. Other rows
are left alone. The transaction rolls back if any write fails.
"""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.database import SessionLocal
from models import CommonCodeModel, ProcessModel, StorageLocationModel, WarehouseMasterModel


PROCESSES = [
    ("LT", "복합선반"),
    ("TP", "탭핑"),
    ("DOT", "세레이션"),
    ("ASSY", "조립"),
    ("INSP", "검사"),
]
WAREHOUSES = [("FG", "완제품 창고"), ("RM", "자재 창고")]
LOCATIONS = [
    ("S-LT", "복합선반"),
    ("S-SLS", "영업"),
    ("S-SCR", "스크랩"),
    ("S-OUT", "외주가공"),
    ("S-AG", "은도금"),
    ("S-AS", "조립"),
    ("S-SMP", "샘플"),
    ("S-NG", "부적합"),
]
COMMON_CODES = [
    ("UNIT", "단위", [("EA", "EA"), ("KG", "KG"), ("MM", "MM"), ("ROLL", "ROLL"), ("BOX", "BOX"), ("SET", "SET")]),
    ("MATERIAL_TYPE", "자재유형", [("RAW", "원재료"), ("SEMI", "반제품"), ("FINISHED", "완제품")]),
    ("ACCOUNT_TYPE", "계정구분", [("PROD", "제품"), ("GOODS", "상품")]),
]


def upsert_master(db, model, code_field, name_field, entries, stamp):
    created = updated = 0
    column = getattr(model, code_field)
    for order, (code, name) in enumerate(entries, 1):
        row = db.query(model).filter(column == code).one_or_none()
        if row is None:
            db.add(model(**{
                code_field: code, name_field: name, "sort_order": order,
                "is_active": "Y", "created_at": stamp,
            }))
            created += 1
        elif getattr(row, name_field) != name:
            setattr(row, name_field, name)
            updated += 1
    return created, updated


def seed(db):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = {
        "공정": upsert_master(db, ProcessModel, "process_code", "process_name", PROCESSES, stamp),
        "창고": upsert_master(db, WarehouseMasterModel, "warehouse_code", "warehouse_name", WAREHOUSES, stamp),
        "저장위치": upsert_master(db, StorageLocationModel, "location_code", "location_name", LOCATIONS, stamp),
    }
    created = updated = 0
    for group, group_name, entries in COMMON_CODES:
        for order, (code, name) in enumerate(entries, 1):
            row = db.query(CommonCodeModel).filter(
                CommonCodeModel.group_code == group, CommonCodeModel.code == code
            ).one_or_none()
            if row is None:
                db.add(CommonCodeModel(
                    group_code=group, group_name=group_name, code=code, code_name=name,
                    sort_order=order, is_active="Y", created_at=stamp,
                ))
                created += 1
            elif row.group_name != group_name or row.code_name != name:
                row.group_name = group_name
                row.code_name = name
                updated += 1
    results["공통 코드"] = created, updated
    db.commit()
    return results


def main():
    db = SessionLocal()
    try:
        results = seed(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    for label, (created, updated) in results.items():
        print(f"{label}: 신규 {created}건, 이름 수정 {updated}건")


if __name__ == "__main__":
    main()
