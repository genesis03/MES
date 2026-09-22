"""SQLite item_id 전환 물리 스키마 점검.

실행:
    python scripts/verify_item_identity_schema.py

DB를 수정하지 않습니다.
업무 테이블에 아직 item_master.part_no를 참조하는 물리 Foreign Key가 남아 있는지,
item_id 컬럼/인덱스가 존재하는지 점검합니다.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from core.database import engine
from models.item_identity_migration import IDENTITY_COLUMNS


def main() -> int:
    if engine.dialect.name != "sqlite":
        print("현재 스크립트는 SQLite 운영 DB 점검용입니다.")
        return 0

    legacy_part_fk = []
    missing_item_columns = []
    missing_item_indexes = []

    with engine.connect() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())

        checked_columns = set()
        for table_name, id_column, _ in IDENTITY_COLUMNS:
            key = (table_name, id_column)
            if key in checked_columns or table_name not in tables:
                continue
            checked_columns.add(key)

            columns = {row["name"] for row in inspector.get_columns(table_name)}
            if id_column not in columns:
                missing_item_columns.append(f"{table_name}.{id_column}")
                continue

            index_columns = {
                col
                for idx in inspector.get_indexes(table_name)
                for col in (idx.get("column_names") or [])
            }
            if id_column not in index_columns:
                missing_item_indexes.append(f"{table_name}.{id_column}")

        for table_name in sorted(tables):
            fk_rows = conn.execute(
                text(f'PRAGMA foreign_key_list("{table_name.replace(chr(34), chr(34)*2)}")')
            ).mappings().all()
            for row in fk_rows:
                if (
                    str(row.get("table") or "") == "item_master"
                    and str(row.get("to") or "") == "part_no"
                ):
                    legacy_part_fk.append(
                        f"{table_name}.{row.get('from')} -> item_master.part_no"
                    )

    print("품목 item_id 물리 스키마 점검")
    print("=" * 72)

    if missing_item_columns:
        print("[확인필요] item_id 계열 컬럼 누락")
        for value in missing_item_columns:
            print(" -", value)
    else:
        print("[OK] item_id 계열 컬럼 누락 없음")

    if missing_item_indexes:
        print("[확인필요] item_id 인덱스 누락")
        for value in missing_item_indexes:
            print(" -", value)
    else:
        print("[OK] item_id 인덱스 누락 없음")

    if legacy_part_fk:
        print("[전환잔여] item_master.part_no 물리 FK")
        for value in legacy_part_fk:
            print(" -", value)
    else:
        print("[OK] item_master.part_no 물리 FK 없음")

    print("=" * 72)
    print(
        f"컬럼누락={len(missing_item_columns)} / "
        f"인덱스누락={len(missing_item_indexes)} / "
        f"part_no FK잔여={len(legacy_part_fk)}"
    )

    return 1 if missing_item_columns or missing_item_indexes or legacy_part_fk else 0


if __name__ == "__main__":
    raise SystemExit(main())
