"""품목 item_id 연결 상태 점검.

실행:
    python scripts/verify_item_identity.py

DB를 수정하지 않고, 각 업무 테이블에서 품번은 존재하지만 item_id 연결이
비어 있는 행 수와 잘못 연결된 행 수를 출력합니다.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from core.database import engine
from models.item_identity_migration import IDENTITY_COLUMNS


def q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def main() -> int:
    total_missing = 0
    total_mismatch = 0

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        print("품목 item_id 연결 점검")
        print("=" * 72)

        for table_name, id_column, part_column in IDENTITY_COLUMNS:
            if table_name not in tables:
                continue

            columns = {c["name"] for c in inspect(conn).get_columns(table_name)}
            if id_column not in columns or part_column not in columns:
                print(f"[컬럼없음] {table_name}.{id_column}")
                continue

            table_q, id_q, part_q = q(table_name), q(id_column), q(part_column)
            missing = conn.execute(text(
                f"""
                SELECT COUNT(*)
                  FROM {table_q}
                 WHERE {part_q} IS NOT NULL
                   AND TRIM({part_q}) <> ''
                   AND {id_q} IS NULL
                   AND EXISTS (
                       SELECT 1 FROM item_master im WHERE im.part_no = {table_q}.{part_q}
                   )
                """
            )).scalar_one()

            mismatch = conn.execute(text(
                f"""
                SELECT COUNT(*)
                  FROM {table_q}
                  JOIN item_master im ON im.id = {table_q}.{id_q}
                 WHERE {id_q} IS NOT NULL
                   AND {part_q} IS NOT NULL
                   AND TRIM({part_q}) <> ''
                   AND im.part_no <> {table_q}.{part_q}
                """
            )).scalar_one()

            total_missing += int(missing or 0)
            total_mismatch += int(mismatch or 0)
            status = "OK" if not missing and not mismatch else "확인필요"
            print(
                f"[{status}] {table_name:32s} "
                f"{id_column:18s} 미연결={missing:4d} 불일치={mismatch:4d}"
            )

    print("=" * 72)
    print(f"총 미연결: {total_missing}건 / 총 불일치: {total_mismatch}건")
    return 0 if total_missing == 0 and total_mismatch == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
