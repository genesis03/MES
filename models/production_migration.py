from sqlalchemy import inspect, text


def ensure_production_order_columns(engine) -> None:
    inspector = inspect(engine)
    if "production_work_orders" not in inspector.get_table_names():
        return

    columns = {col["name"] for col in inspector.get_columns("production_work_orders")}
    if "priority" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE production_work_orders ADD COLUMN priority INTEGER NOT NULL DEFAULT 4"))
