from sqlalchemy import inspect, text


def _sqlite_rebuild_packing_boxes(engine) -> None:
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.execute("DROP TABLE IF EXISTS packing_boxes_new")
        cursor.execute(
            """
            CREATE TABLE packing_boxes_new (
                id INTEGER NOT NULL PRIMARY KEY,
                packing_id INTEGER NOT NULL,
                box_no INTEGER NOT NULL,
                package_lot_no VARCHAR(60) NOT NULL,
                box_qty FLOAT NOT NULL,
                FOREIGN KEY(packing_id) REFERENCES packing_masters (id)
            )
            """
        )
        cursor.execute(
            """
            INSERT INTO packing_boxes_new (id, packing_id, box_no, package_lot_no, box_qty)
            SELECT id, packing_id, box_no, package_lot_no, box_qty
            FROM packing_boxes
            """
        )
        cursor.execute("DROP TABLE packing_boxes")
        cursor.execute("ALTER TABLE packing_boxes_new RENAME TO packing_boxes")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_packing_boxes_packing_id ON packing_boxes (packing_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_packing_boxes_package_lot_no ON packing_boxes (package_lot_no)")
        raw.commit()
        cursor.execute("PRAGMA foreign_keys=ON")
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def ensure_packing_lot_scope(engine) -> None:
    """포장 LOT의 전역 UNIQUE를 제거합니다.

    업무 규칙상 포장 LOT(=출고 LOT)는 `품번 + LOT` 조합으로 식별하므로
    서로 다른 품번은 같은 YYMMDD01xxx 번호를 사용할 수 있습니다.
    """
    inspector = inspect(engine)
    if "packing_boxes" not in inspector.get_table_names():
        return

    unique_constraints = inspector.get_unique_constraints("packing_boxes")
    target_constraints = [
        row for row in unique_constraints
        if list(row.get("column_names") or []) == ["package_lot_no"]
    ]
    unique_indexes = [
        row for row in inspector.get_indexes("packing_boxes")
        if row.get("unique") and list(row.get("column_names") or []) == ["package_lot_no"]
    ]
    if not target_constraints and not unique_indexes:
        return

    if engine.dialect.name == "sqlite":
        _sqlite_rebuild_packing_boxes(engine)
        return

    preparer = engine.dialect.identifier_preparer
    constraint_names = {row.get("name") for row in target_constraints if row.get("name")}
    with engine.begin() as conn:
        for constraint in target_constraints:
            name = constraint.get("name")
            if name:
                conn.execute(text(
                    f"ALTER TABLE {preparer.quote('packing_boxes')} "
                    f"DROP CONSTRAINT {preparer.quote(name)}"
                ))
        # DB에 따라 UNIQUE constraint와 backing index가 둘 다 inspector에 보일 수 있습니다.
        # constraint와 같은 이름의 index는 constraint 삭제 시 함께 제거되므로 중복 DROP하지 않습니다.
        for index in unique_indexes:
            name = index.get("name")
            if name and name not in constraint_names:
                conn.execute(text(f"DROP INDEX {preparer.quote(name)}"))
