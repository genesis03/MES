from sqlalchemy import inspect, text


def _add_column(engine, table_name: str, column_sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}"))


def ensure_sales_policy_columns(engine) -> None:
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if "sales_order_masters" in tables:
        columns = {col["name"] for col in inspector.get_columns("sales_order_masters")}
        additions = {
            "order_type": "order_type VARCHAR(20) NOT NULL DEFAULT 'NORMAL'",
            "transaction_type": "transaction_type VARCHAR(20) NOT NULL DEFAULT 'PAID'",
            "po_no": "po_no VARCHAR(100)",
        }
        for name, column_sql in additions.items():
            if name not in columns:
                _add_column(engine, "sales_order_masters", column_sql)

    if "shipment_masters" in tables:
        columns = {col["name"] for col in inspect(engine).get_columns("shipment_masters")}
        additions = {
            "fifo_exception": "fifo_exception VARCHAR(1) NOT NULL DEFAULT 'N'",
            "fifo_exception_reason": "fifo_exception_reason TEXT",
        }
        for name, column_sql in additions.items():
            if name not in columns:
                _add_column(engine, "shipment_masters", column_sql)

    if "shipment_direct_lots" in tables:
        columns = {col["name"] for col in inspect(engine).get_columns("shipment_direct_lots")}
        additions = {
            "outbound_lot_no": "outbound_lot_no VARCHAR(60)",
            "box_no": "box_no INTEGER",
        }
        for name, column_sql in additions.items():
            if name not in columns:
                _add_column(engine, "shipment_direct_lots", column_sql)
