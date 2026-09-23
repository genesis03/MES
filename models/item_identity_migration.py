"""품목 영구 내부키(item_master.id) 전환용 호환 마이그레이션.

기존 part_no 문자열 컬럼은 당시값/호환용으로 유지하고, 각 업무 테이블에
item_id 계열 컬럼을 추가하여 현재 품목 마스터의 id를 백필합니다.
이 단계에서는 기존 조회/저장 로직을 강제로 바꾸지 않아 기존 기능을 보존합니다.
"""

from datetime import datetime
from pathlib import Path
import re
import sqlite3

from sqlalchemy import inspect, text


IDENTITY_COLUMNS = (
    ("manual_labels", "item_id", "part_no"),
    ("item_boms", "parent_item_id", "parent_part_no"),
    ("item_boms", "child_item_id", "child_part_no"),
    ("purchase_order_items", "item_id", "part_no"),
    ("purchase_inbound_items", "item_id", "part_no"),
    ("purchase_items", "item_id", "part_no"),
    ("production_plans", "item_id", "part_no"),
    ("production_work_orders", "item_id", "part_no"),
    ("production_lots", "item_id", "part_no"),
    ("production_run_materials", "material_item_id", "material_part_no"),
    ("lot_consumptions", "item_id", "part_no"),
    ("quality_inbound_standards", "item_id", "part_no"),
    ("sales_order_items", "item_id", "part_no"),
    ("shipment_items", "item_id", "part_no"),
    ("shipment_direct_lots", "source_item_id", "source_part_no"),
    ("packing_masters", "item_id", "part_no"),
    ("shipping_lot_registry", "item_id", "part_no"),
    ("subcontract_order_items", "previous_item_id", "previous_part_no"),
    ("subcontract_order_items", "item_id", "order_part_no"),
    ("subcontract_outbound_items", "previous_item_id", "previous_part_no"),
    ("subcontract_outbound_items", "item_id", "order_part_no"),
    ("subcontract_inbound_items", "previous_item_id", "previous_part_no"),
    ("subcontract_inbound_items", "item_id", "part_no"),
)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ensure_item_identity_columns(engine) -> None:
    """item_id 계열 컬럼/인덱스를 추가하고 기존 데이터를 백필합니다."""
    if engine.dialect.name not in ("sqlite", "postgresql"):
        return

    with engine.begin() as conn:
        tables = set(inspect(conn).get_table_names())
        if "item_master" not in tables:
            return

        for table_name, id_column, part_column in IDENTITY_COLUMNS:
            if table_name not in tables:
                continue

            columns = {col["name"] for col in inspect(conn).get_columns(table_name)}
            if part_column not in columns:
                continue

            table_q = _quote(table_name)
            id_q = _quote(id_column)
            part_q = _quote(part_column)

            if id_column not in columns:
                if engine.dialect.name == "postgresql":
                    conn.execute(text(
                        f"ALTER TABLE {table_q} ADD COLUMN IF NOT EXISTS {id_q} INTEGER"
                    ))
                else:
                    conn.execute(text(
                        f"ALTER TABLE {table_q} ADD COLUMN {id_q} INTEGER"
                    ))

            conn.execute(text(
                f"""
                UPDATE {table_q}
                   SET {id_q} = (
                       SELECT im.id
                         FROM item_master im
                        WHERE im.part_no = {table_q}.{part_q}
                        LIMIT 1
                   )
                 WHERE {id_q} IS NULL
                   AND {part_q} IS NOT NULL
                   AND TRIM({part_q}) <> ''
                   AND EXISTS (
                       SELECT 1
                         FROM item_master im
                        WHERE im.part_no = {table_q}.{part_q}
                   )
                """
            ))

            index_name = f"ix_{table_name}_{id_column}"
            conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS {_quote(index_name)} "
                f"ON {table_q} ({id_q})"
            ))



def _split_sql_definitions(body: str) -> list[str]:
    parts = []
    start = 0
    depth = 0
    quote = None
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == quote:
                if i + 1 < len(body) and body[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            quote = ch
        elif ch == "[":
            quote = "]"
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        elif ch == "," and depth == 0:
            parts.append(body[start:i].strip())
            start = i + 1
        i += 1
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _legacy_part_fk_tables(conn) -> list[str]:
    tables = inspect(conn).get_table_names()
    result = []
    for table_name in tables:
        rows = conn.execute(
            text(f"PRAGMA foreign_key_list({_quote(table_name)})")
        ).mappings().all()
        if any(
            str(row.get("table") or "") == "item_master"
            and str(row.get("to") or "") == "part_no"
            for row in rows
        ):
            result.append(table_name)
    return sorted(set(result))


def _rewrite_create_sql_without_part_fk(create_sql: str, table_name: str, temp_name: str) -> str:
    open_pos = create_sql.find("(")
    close_pos = create_sql.rfind(")")
    if open_pos < 0 or close_pos <= open_pos:
        raise RuntimeError(f"{table_name}: CREATE TABLE SQL 형식을 해석할 수 없습니다.")

    head = create_sql[:open_pos]
    body = create_sql[open_pos + 1:close_pos]
    tail = create_sql[close_pos + 1:]

    defs = _split_sql_definitions(body)
    kept = []
    removed = 0
    for definition in defs:
        normalized = re.sub(r'["`\[\]]', "", definition).lower()
        if (
            "foreign key" in normalized
            and "references item_master" in normalized
            and re.search(r"references\s+item_master\s*\(\s*part_no\s*\)", normalized)
        ):
            removed += 1
            continue

        # 과거 SQLite가 inline REFERENCES 형식으로 만든 경우도 제거합니다.
        if (
            "references item_master" in normalized
            and re.search(r"references\s+item_master\s*\(\s*part_no\s*\)", normalized)
        ):
            definition = re.sub(
                r'\s+REFERENCES\s+["`\[]?item_master["`\]]?\s*'
                r'\(\s*["`\[]?part_no["`\]]?\s*\)'
                r'(?:\s+ON\s+(?:DELETE|UPDATE)\s+\w+)*',
                "",
                definition,
                flags=re.IGNORECASE,
            )
            removed += 1

        # 품질기준은 기존 품번+Revision 유일키를 영구 item_id+Revision으로 전환합니다.
        if table_name == "quality_inbound_standards":
            definition = re.sub(
                r'CONSTRAINT\s+["`\[]?uq_quality_inbound_standard_part_revision["`\]]?'
                r'\s+UNIQUE\s*\(\s*["`\[]?part_no["`\]]?\s*,\s*'
                r'["`\[]?revision["`\]]?\s*\)',
                'CONSTRAINT uq_quality_inbound_standard_item_revision UNIQUE (item_id, revision)',
                definition,
                flags=re.IGNORECASE,
            )
            definition = re.sub(
                r'UNIQUE\s*\(\s*["`\[]?part_no["`\]]?\s*,\s*'
                r'["`\[]?revision["`\]]?\s*\)',
                'UNIQUE (item_id, revision)',
                definition,
                flags=re.IGNORECASE,
            )

        kept.append(definition)

    if removed <= 0:
        raise RuntimeError(f"{table_name}: 제거할 item_master.part_no FK 정의를 찾지 못했습니다.")

    table_pattern = re.compile(
        r'^(\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)'
        + r'(?:"' + re.escape(table_name) + r'"|`' + re.escape(table_name)
        + r'`|\[' + re.escape(table_name) + r'\]|' + re.escape(table_name) + r')',
        flags=re.IGNORECASE,
    )
    new_head, count = table_pattern.subn(
        lambda match: match.group(1) + _quote(temp_name),
        head,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"{table_name}: 임시 테이블 CREATE SQL을 만들 수 없습니다.")

    return new_head + "(" + ",\n".join(kept) + ")" + tail


def _backup_sqlite_database(conn, engine) -> Path | None:
    database = engine.url.database
    if not database or database == ":memory:":
        return None

    source_path = Path(database).resolve()
    if not source_path.exists():
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source_path.with_name(
        source_path.name + f".pre_item_id_fk_{stamp}.bak"
    )
    driver = conn.connection.driver_connection
    with sqlite3.connect(str(backup_path)) as backup:
        driver.backup(backup)
    return backup_path


def ensure_item_part_no_fk_removed(engine) -> None:
    """SQLite의 레거시 item_master.part_no 물리 FK를 안전하게 제거합니다.

    item_id 백필이 끝난 뒤에만 수행하며, 실제 FK가 남아 있는 테이블만
    원본 CREATE TABLE 정의를 보존한 채 재구성합니다. 실행 전 DB 백업을
    한 번 생성하고, 행수와 foreign_key_check를 검증한 뒤 커밋합니다.
    """
    if engine.dialect.name != "sqlite":
        return

    with engine.connect() as conn:
        legacy_tables = _legacy_part_fk_tables(conn)
        if not legacy_tables:
            return

        # FK 제거 전에 모든 item_id 연결 상태가 정상인지 확인합니다.
        for table_name, id_column, part_column in IDENTITY_COLUMNS:
            if table_name not in legacy_tables:
                continue
            columns = {col["name"] for col in inspect(conn).get_columns(table_name)}
            if id_column not in columns or part_column not in columns:
                raise RuntimeError(f"{table_name}: item_id 전환 컬럼이 준비되지 않았습니다.")
            unresolved = conn.execute(text(
                f"""
                SELECT COUNT(*)
                  FROM {_quote(table_name)}
                 WHERE {_quote(part_column)} IS NOT NULL
                   AND TRIM({_quote(part_column)}) <> ''
                   AND {_quote(id_column)} IS NULL
                """
            )).scalar_one()
            if unresolved:
                raise RuntimeError(
                    f"{table_name}: item_id 미연결 {int(unresolved)}건이 있어 FK 제거를 중단했습니다."
                )

        backup_path = _backup_sqlite_database(conn, engine)

        # 위의 조회로 열린 암묵적 트랜잭션을 먼저 종료해야 SQLite가
        # PRAGMA foreign_keys=OFF 변경을 실제로 적용합니다.
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()

        fk_state = int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() or 0)
        conn.commit()
        if fk_state != 0:
            raise RuntimeError("SQLite foreign_keys 비활성화에 실패해 스키마 전환을 중단했습니다.")

        transaction = conn.begin()
        try:
            for table_name in legacy_tables:
                table_q = _quote(table_name)
                create_sql = conn.execute(
                    text(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='table' AND name=:name"
                    ),
                    {"name": table_name},
                ).scalar_one_or_none()
                if not create_sql:
                    raise RuntimeError(f"{table_name}: CREATE TABLE SQL을 찾을 수 없습니다.")

                row_count_before = int(
                    conn.execute(text(f"SELECT COUNT(*) FROM {table_q}")).scalar_one() or 0
                )

                dependent_sql = [
                    row[0]
                    for row in conn.execute(
                        text(
                            """
                            SELECT sql
                              FROM sqlite_master
                             WHERE tbl_name=:name
                               AND type IN ('index','trigger')
                               AND sql IS NOT NULL
                             ORDER BY type, name
                            """
                        ),
                        {"name": table_name},
                    ).all()
                    if row[0]
                ]

                temp_name = f"__item_id_migrate_{table_name}"
                conn.execute(text(f"DROP TABLE IF EXISTS {_quote(temp_name)}"))
                rewritten = _rewrite_create_sql_without_part_fk(
                    create_sql, table_name, temp_name
                )
                conn.exec_driver_sql(rewritten)

                columns = [row["name"] for row in inspect(conn).get_columns(table_name)]
                column_list = ", ".join(_quote(name) for name in columns)
                conn.execute(text(
                    f"INSERT INTO {_quote(temp_name)} ({column_list}) "
                    f"SELECT {column_list} FROM {table_q}"
                ))

                row_count_after = int(
                    conn.execute(
                        text(f"SELECT COUNT(*) FROM {_quote(temp_name)}")
                    ).scalar_one()
                    or 0
                )
                if row_count_after != row_count_before:
                    raise RuntimeError(
                        f"{table_name}: 재구성 전후 행수가 다릅니다. "
                        f"{row_count_before} -> {row_count_after}"
                    )

                conn.execute(text(f"DROP TABLE {table_q}"))
                conn.execute(text(
                    f"ALTER TABLE {_quote(temp_name)} RENAME TO {table_q}"
                ))

                for sql in dependent_sql:
                    conn.exec_driver_sql(sql)

            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()

        remaining = _legacy_part_fk_tables(conn)
        if remaining:
            raise RuntimeError(
                "item_master.part_no FK 제거 후에도 잔여 테이블이 있습니다: "
                + ", ".join(remaining)
            )

        fk_errors = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(
                f"item_id 스키마 전환 후 FK 무결성 오류 {len(fk_errors)}건이 발견되었습니다."
            )

        if backup_path:
            print(f"[item_id] SQLite 스키마 전환 전 백업: {backup_path}")
        print(
            "[item_id] item_master.part_no 물리 FK 제거 완료: "
            + ", ".join(legacy_tables)
        )


# 실제 업무 생성 경로와 운영 DB 검증을 모두 통과한 item_id 계열만
# 물리 NOT NULL 대상으로 강제합니다. production_lots는 과거 호환 때문에 제외합니다.
ITEM_ID_NOT_NULL_COLUMNS = (
    ("purchase_order_items", "item_id"),
    ("purchase_inbound_items", "item_id"),
    ("production_plans", "item_id"),
    ("production_work_orders", "item_id"),
    ("production_run_materials", "material_item_id"),
    ("lot_consumptions", "item_id"),
    ("packing_masters", "item_id"),
    ("sales_order_items", "item_id"),
    ("shipment_items", "item_id"),
    ("subcontract_order_items", "previous_item_id"),
    ("subcontract_order_items", "item_id"),
    ("subcontract_outbound_items", "previous_item_id"),
    ("subcontract_outbound_items", "item_id"),
    ("subcontract_inbound_items", "previous_item_id"),
    ("subcontract_inbound_items", "item_id"),
)


def _rewrite_create_sql_with_not_null(
    create_sql: str,
    table_name: str,
    temp_name: str,
    columns: set[str],
) -> str:
    open_pos = create_sql.find("(")
    close_pos = create_sql.rfind(")")
    if open_pos < 0 or close_pos <= open_pos:
        raise RuntimeError(f"{table_name}: CREATE TABLE SQL 형식을 해석할 수 없습니다.")

    head = create_sql[:open_pos]
    body = create_sql[open_pos + 1:close_pos]
    tail = create_sql[close_pos + 1:]

    defs = _split_sql_definitions(body)
    changed = set()
    rewritten_defs = []
    for definition in defs:
        name_match = re.match(
            r'^\s*(?:"([^"]+)"|\x60([^\x60]+)\x60|\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*))\s+',
            definition,
        )
        column_name = next((value for value in (name_match.groups() if name_match else ()) if value), None)
        if column_name in columns:
            if "not null" not in definition.lower():
                ident = (
                    r'(?:"' + re.escape(column_name) + r'"|\x60' + re.escape(column_name)
                    + r'\x60|\[' + re.escape(column_name) + r'\]|' + re.escape(column_name) + r')'
                )
                type_pattern = re.compile(
                    r'^(\s*' + ident + r'\s+[A-Za-z0-9_]+(?:\s*\([^)]*\))?)',
                    flags=re.IGNORECASE,
                )
                definition, count = type_pattern.subn(r'\1 NOT NULL', definition, count=1)
                if count != 1:
                    raise RuntimeError(
                        f"{table_name}.{column_name}: NOT NULL 정의를 만들 수 없습니다."
                    )
            changed.add(column_name)
        rewritten_defs.append(definition)

    missing = columns - changed
    if missing:
        raise RuntimeError(
            f"{table_name}: NOT NULL 대상 컬럼 정의를 찾지 못했습니다: "
            + ", ".join(sorted(missing))
        )

    table_pattern = re.compile(
        r'^(\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)'
        + r'(?:"' + re.escape(table_name) + r'"|\x60' + re.escape(table_name)
        + r'\x60|\[' + re.escape(table_name) + r'\]|' + re.escape(table_name) + r')',
        flags=re.IGNORECASE,
    )
    new_head, count = table_pattern.subn(
        lambda match: match.group(1) + _quote(temp_name),
        head,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"{table_name}: 임시 테이블 CREATE SQL을 만들 수 없습니다.")

    return new_head + "(" + ",\n".join(rewritten_defs) + ")" + tail


def _backup_sqlite_database_for_not_null(conn, engine) -> Path | None:
    database = engine.url.database
    if not database or database == ":memory:":
        return None

    source_path = Path(database).resolve()
    if not source_path.exists():
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = source_path.with_name(
        source_path.name + f".pre_item_id_not_null_{stamp}.bak"
    )
    driver = conn.connection.driver_connection
    with sqlite3.connect(str(backup_path)) as backup:
        driver.backup(backup)
    return backup_path


def ensure_item_identity_not_null(engine) -> None:
    """검증 완료된 item_id 계열 컬럼을 실제 DB에서 NOT NULL로 강제합니다.

    NULL 데이터가 단 1건이라도 있으면 변경하지 않습니다.
    SQLite는 변경 전 DB 전체 백업 후 대상 테이블만 재구성하고,
    PostgreSQL은 컬럼별 SET NOT NULL을 적용합니다.
    """
    grouped: dict[str, set[str]] = {}
    for table_name, column_name in ITEM_ID_NOT_NULL_COLUMNS:
        grouped.setdefault(table_name, set()).add(column_name)

    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            tables = set(inspect(conn).get_table_names())
            for table_name, columns in grouped.items():
                if table_name not in tables:
                    continue
                existing = {col["name"]: col for col in inspect(conn).get_columns(table_name)}
                for column_name in columns:
                    if column_name not in existing:
                        raise RuntimeError(f"{table_name}.{column_name}: 컬럼이 없습니다.")
                    unresolved = int(conn.execute(text(
                        f"SELECT COUNT(*) FROM {_quote(table_name)} "
                        f"WHERE {_quote(column_name)} IS NULL"
                    )).scalar_one() or 0)
                    if unresolved:
                        raise RuntimeError(
                            f"{table_name}.{column_name}: NULL {unresolved}건이 있어 NOT NULL 전환을 중단했습니다."
                        )
                    if existing[column_name].get("nullable", True):
                        conn.execute(text(
                            f"ALTER TABLE {_quote(table_name)} "
                            f"ALTER COLUMN {_quote(column_name)} SET NOT NULL"
                        ))
        return

    if engine.dialect.name != "sqlite":
        return

    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        pending: dict[str, set[str]] = {}

        for table_name, columns in grouped.items():
            if table_name not in tables:
                continue
            info_rows = conn.exec_driver_sql(
                f"PRAGMA table_info({_quote(table_name)})"
            ).mappings().all()
            info = {str(row["name"]): row for row in info_rows}
            for column_name in columns:
                if column_name not in info:
                    raise RuntimeError(f"{table_name}.{column_name}: 컬럼이 없습니다.")
                unresolved = int(conn.execute(text(
                    f"SELECT COUNT(*) FROM {_quote(table_name)} "
                    f"WHERE {_quote(column_name)} IS NULL"
                )).scalar_one() or 0)
                if unresolved:
                    raise RuntimeError(
                        f"{table_name}.{column_name}: NULL {unresolved}건이 있어 NOT NULL 전환을 중단했습니다."
                    )
                if int(info[column_name].get("notnull") or 0) == 0:
                    pending.setdefault(table_name, set()).add(column_name)

        if not pending:
            return

        backup_path = _backup_sqlite_database_for_not_null(conn, engine)

        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.commit()
        if int(conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() or 0) != 0:
            raise RuntimeError("SQLite foreign_keys 비활성화에 실패해 NOT NULL 전환을 중단했습니다.")
        conn.commit()

        transaction = conn.begin()
        try:
            for table_name, columns in pending.items():
                table_q = _quote(table_name)
                create_sql = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                    {"name": table_name},
                ).scalar_one_or_none()
                if not create_sql:
                    raise RuntimeError(f"{table_name}: CREATE TABLE SQL을 찾을 수 없습니다.")

                row_count_before = int(
                    conn.execute(text(f"SELECT COUNT(*) FROM {table_q}")).scalar_one() or 0
                )
                dependent_sql = [
                    row[0]
                    for row in conn.execute(
                        text(
                            """
                            SELECT sql
                              FROM sqlite_master
                             WHERE tbl_name=:name
                               AND type IN ('index','trigger')
                               AND sql IS NOT NULL
                             ORDER BY type, name
                            """
                        ),
                        {"name": table_name},
                    ).all()
                    if row[0]
                ]

                temp_name = f"__item_id_not_null_{table_name}"
                conn.execute(text(f"DROP TABLE IF EXISTS {_quote(temp_name)}"))
                rewritten = _rewrite_create_sql_with_not_null(
                    create_sql, table_name, temp_name, columns
                )
                conn.exec_driver_sql(rewritten)

                column_names = [row["name"] for row in inspect(conn).get_columns(table_name)]
                column_list = ", ".join(_quote(name) for name in column_names)
                conn.execute(text(
                    f"INSERT INTO {_quote(temp_name)} ({column_list}) "
                    f"SELECT {column_list} FROM {table_q}"
                ))

                row_count_after = int(
                    conn.execute(text(f"SELECT COUNT(*) FROM {_quote(temp_name)}")).scalar_one()
                    or 0
                )
                if row_count_after != row_count_before:
                    raise RuntimeError(
                        f"{table_name}: NOT NULL 전환 전후 행수가 다릅니다. "
                        f"{row_count_before} -> {row_count_after}"
                    )

                conn.execute(text(f"DROP TABLE {table_q}"))
                conn.execute(text(
                    f"ALTER TABLE {_quote(temp_name)} RENAME TO {table_q}"
                ))
                for sql in dependent_sql:
                    conn.exec_driver_sql(sql)

            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
        finally:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            conn.commit()

        fk_errors = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(
                f"item_id NOT NULL 전환 후 FK 무결성 오류 {len(fk_errors)}건이 발견되었습니다."
            )

        for table_name, columns in pending.items():
            info_rows = conn.exec_driver_sql(
                f"PRAGMA table_info({_quote(table_name)})"
            ).mappings().all()
            info = {str(row["name"]): row for row in info_rows}
            failed = [
                column_name for column_name in columns
                if int(info.get(column_name, {}).get("notnull") or 0) != 1
            ]
            if failed:
                raise RuntimeError(
                    f"{table_name}: NOT NULL 적용 확인 실패: " + ", ".join(failed)
                )

        if backup_path:
            print(f"[item_id] NOT NULL 전환 전 백업: {backup_path}")
        print(
            "[item_id] NOT NULL 전환 완료: "
            + ", ".join(
                f"{table}.{column}"
                for table, columns in pending.items()
                for column in sorted(columns)
            )
        )
