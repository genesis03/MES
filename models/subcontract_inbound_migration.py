from sqlalchemy import inspect, text


def ensure_subcontract_inbound_columns(engine):
    """기존 SQLite/PostgreSQL DB에 외주입고 추가 컬럼을 보강합니다."""
    inspector = inspect(engine)
    if "subcontract_inbound_lots" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("subcontract_inbound_lots")}
    statements = []
    if "supplier_lot_no" not in existing:
        statements.append("ALTER TABLE subcontract_inbound_lots ADD COLUMN supplier_lot_no VARCHAR(100)")
    if "sample_qty" not in existing:
        statements.append("ALTER TABLE subcontract_inbound_lots ADD COLUMN sample_qty FLOAT NOT NULL DEFAULT 0")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
