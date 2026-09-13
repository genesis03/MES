"""Add optional purchase entry columns to existing SQLite databases."""

from sqlalchemy import inspect, text


def ensure_purchase_entry_columns(engine):
    if engine.dialect.name not in ("sqlite", "postgresql"):
        return
    columns = {
        "purchase_order_masters": {"manager_name": "VARCHAR(50)"},
        "purchase_order_items": {
            "delivery_date": "VARCHAR(10)",
            "note": "TEXT",
        },
        "purchase_inbound_masters": {
            "status": "VARCHAR(20) NOT NULL DEFAULT 'CONFIRMED'",
            "note": "TEXT",
        },
        "purchase_inbound_items": {"note": "TEXT"},
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, additions in columns.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, data_type in additions.items():
                if name not in existing:
                    clause = "ADD COLUMN IF NOT EXISTS" if engine.dialect.name == "postgresql" else "ADD COLUMN"
                    connection.execute(text(f"ALTER TABLE {table} {clause} {name} {data_type}"))

