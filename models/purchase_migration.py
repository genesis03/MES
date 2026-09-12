"""Add optional purchase entry columns to existing SQLite databases."""

from sqlalchemy import inspect, text


def ensure_purchase_entry_columns(engine):
    if engine.dialect.name != "sqlite":
        return
    columns = {
        "purchase_order_masters": {"manager_name": "VARCHAR(50)"},
        "purchase_order_items": {
            "delivery_date": "VARCHAR(10)",
            "note": "TEXT",
        },
    }
    with engine.begin() as connection:
        inspector = inspect(connection)
        for table, additions in columns.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, data_type in additions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {data_type}"))

