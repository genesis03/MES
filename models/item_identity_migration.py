"""품목 영구 내부키(item_master.id) 전환용 호환 마이그레이션.

기존 part_no 문자열 컬럼은 당시값/호환용으로 유지하고, 각 업무 테이블에
item_id 계열 컬럼을 추가하여 현재 품목 마스터의 id를 백필합니다.
이 단계에서는 기존 조회/저장 로직을 강제로 바꾸지 않아 기존 기능을 보존합니다.
"""

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
