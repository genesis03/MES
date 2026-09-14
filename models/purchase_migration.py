"""Add optional purchase entry columns to existing SQLite/PostgreSQL databases."""

from sqlalchemy import inspect, text


def _make_inbound_warehouse_nullable(connection, dialect):
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("purchase_inbound_items")}
    warehouse = columns.get("warehouse_code")
    if not warehouse or warehouse.get("nullable", True):
        return

    if dialect == "postgresql":
        connection.execute(text(
            "ALTER TABLE purchase_inbound_items ALTER COLUMN warehouse_code DROP NOT NULL"
        ))
        return

    connection.execute(text("ALTER TABLE purchase_inbound_items RENAME TO purchase_inbound_items_old"))
    connection.execute(text("""
        CREATE TABLE purchase_inbound_items (
            id INTEGER NOT NULL PRIMARY KEY,
            inbound_id INTEGER NOT NULL,
            po_item_id INTEGER,
            part_no VARCHAR(50) NOT NULL,
            unit VARCHAR(10) NOT NULL,
            inspection_status VARCHAR(20) NOT NULL,
            inbound_qty FLOAT NOT NULL,
            supplier_lot_no VARCHAR(100) NOT NULL,
            internal_lot_no VARCHAR(100),
            note TEXT,
            warehouse_code VARCHAR(20),
            storage_location VARCHAR(20) NOT NULL,
            unit_price FLOAT NOT NULL,
            CHECK (inbound_qty > 0 AND unit_price >= 0),
            CHECK (length(trim(supplier_lot_no)) > 0),
            FOREIGN KEY(inbound_id) REFERENCES purchase_inbound_masters(id),
            FOREIGN KEY(po_item_id) REFERENCES purchase_order_items(id),
            FOREIGN KEY(part_no) REFERENCES item_master(part_no),
            FOREIGN KEY(warehouse_code) REFERENCES warehouse_masters(warehouse_code),
            FOREIGN KEY(storage_location) REFERENCES storage_locations(location_code)
        )
    """))
    connection.execute(text("""
        INSERT INTO purchase_inbound_items (
            id, inbound_id, po_item_id, part_no, unit, inspection_status,
            inbound_qty, supplier_lot_no, internal_lot_no, note,
            warehouse_code, storage_location, unit_price
        )
        SELECT
            id, inbound_id, po_item_id, part_no, unit, inspection_status,
            inbound_qty, supplier_lot_no, internal_lot_no, note,
            warehouse_code, storage_location, unit_price
        FROM purchase_inbound_items_old
    """))
    connection.execute(text("DROP TABLE purchase_inbound_items_old"))
    for column in ("inbound_id", "po_item_id", "part_no", "supplier_lot_no", "internal_lot_no"):
        connection.execute(text(
            f"CREATE INDEX IF NOT EXISTS ix_purchase_inbound_items_{column} "
            f"ON purchase_inbound_items ({column})"
        ))


def ensure_purchase_entry_columns(engine):
    if engine.dialect.name not in ("sqlite", "postgresql"):
        return
    columns = {
        "purchase_order_masters": {"manager_name": "VARCHAR(50)"},
        "purchase_order_items": {
            "delivery_date": "VARCHAR(10)",
            "warehouse_code": "VARCHAR(20)",
            "storage_location": "VARCHAR(20)",
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
        _make_inbound_warehouse_nullable(connection, engine.dialect.name)
