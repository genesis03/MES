from sqlalchemy import inspect, text


def _add_column(engine, table_name: str, column_sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))


def ensure_production_order_columns(engine) -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "production_work_orders" in tables:
        columns = {col["name"] for col in inspector.get_columns("production_work_orders")}
        if "priority" not in columns:
            _add_column(engine, "production_work_orders", "priority INTEGER NOT NULL DEFAULT 4")

    if "production_performances" not in tables:
        return

    columns = {col["name"] for col in inspect(engine).get_columns("production_performances")}
    additions = {
        "shift_type": "shift_type VARCHAR(10)",
        "operator_id": "operator_id INTEGER",
        "equipment_id": "equipment_id INTEGER",
        "equipment_code": "equipment_code VARCHAR(30)",
        "equipment_name": "equipment_name VARCHAR(100)",
        "source_lot_no": "source_lot_no VARCHAR(100)",
        "setup_qty": "setup_qty FLOAT NOT NULL DEFAULT 0",
        "consumed_qty": "consumed_qty FLOAT NOT NULL DEFAULT 0",
    }
    for name, column_sql in additions.items():
        if name not in columns:
            _add_column(engine, "production_performances", column_sql)
