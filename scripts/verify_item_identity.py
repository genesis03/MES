"""품목 item_id 연결 상태 점검.

실행:
    python scripts/verify_item_identity.py

DB를 수정하지 않고, 각 업무 테이블의 item_id 연결 상태를 점검합니다.
미연결, 품번 불일치, 존재하지 않는 item_id(고아 연결)를 출력합니다.
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
    total_orphan = 0

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
                 WHERE {table_q}.{id_q} IS NOT NULL
                   AND {table_q}.{part_q} IS NOT NULL
                   AND TRIM({table_q}.{part_q}) <> ''
                   AND im.part_no <> {table_q}.{part_q}
                """
            )).scalar_one()

            orphan = conn.execute(text(
                f"""
                SELECT COUNT(*)
                  FROM {table_q}
             LEFT JOIN item_master im ON im.id = {table_q}.{id_q}
                 WHERE {table_q}.{id_q} IS NOT NULL
                   AND im.id IS NULL
                """
            )).scalar_one()

            total_missing += int(missing or 0)
            total_mismatch += int(mismatch or 0)
            total_orphan += int(orphan or 0)
            status = "OK" if not missing and not mismatch and not orphan else "확인필요"
            print(
                f"[{status}] {table_name:32s} "
                f"{id_column:18s} 미연결={missing:4d} 불일치={mismatch:4d} 고아={orphan:4d}"
            )

    history_orphan = 0
    if "item_part_no_history" in tables:
        history_orphan = int(conn.execute(text(
            """
            SELECT COUNT(*)
              FROM item_part_no_history h
         LEFT JOIN item_master im ON im.id = h.item_id
             WHERE im.id IS NULL
            """
        )).scalar_one() or 0)
        total_orphan += history_orphan

    print("=" * 72)
    print(
        f"총 미연결: {total_missing}건 / "
        f"총 불일치: {total_mismatch}건 / "
        f"총 고아연결: {total_orphan}건"
    )
    return 0 if total_missing == 0 and total_mismatch == 0 and total_orphan == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
