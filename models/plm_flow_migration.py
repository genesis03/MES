"""기존 FMEA/공정 내용은 보존하고 연결/기호/비교용 추가 컬럼만 생성합니다."""
from sqlalchemy import inspect, text

COLUMNS = {
    "process_flow_steps": {
        "symbol_code": "VARCHAR(50)",
        "symbol_name_snapshot": "VARCHAR(200)",
        "symbol_shape_snapshot": "VARCHAR(50)",
    },
    "fmea_revisions": {
        "flow_revision_id": "INTEGER REFERENCES process_flow_revisions(id)",
        "flow_snapshot_json": "TEXT",
        "diff_tracking": "BOOLEAN NOT NULL DEFAULT FALSE",
        "vehicle_model_snapshot": "VARCHAR(100)",
        "process_owner": "VARCHAR(100)",
        "completion_due_date": "DATE",
        "mass_production_date": "DATE",
    },
    "fmea_rows": {
        "flow_step_id": "INTEGER REFERENCES process_flow_steps(id)",
        "flow_step_name_snapshot": "VARCHAR(200)",
        "previous_row_id": "INTEGER REFERENCES fmea_rows(id)",
        "action_not_applicable": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
}


def ensure_plm_flow_columns(engine):
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError("공정흐름도 연결 스키마는 SQLite/PostgreSQL 환경을 지원합니다.")
    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table_name, columns in COLUMNS.items():
            if table_name not in tables:
                continue
            present = {x["name"] for x in inspector.get_columns(table_name)}
            for name, definition in columns.items():
                if name not in present:
                    connection.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{name}" {definition}'))
    # 명시적 FK로 연결된 당시 공정명만 보존합니다. 연결/번호/점수는 추정하거나 변경하지 않습니다.
    with engine.begin() as connection:
        tables = set(inspect(connection).get_table_names())
        if {"fmea_rows", "process_flow_steps"}.issubset(tables):
            connection.execute(text("""
                UPDATE fmea_rows SET flow_step_name_snapshot = (
                    SELECT step_name FROM process_flow_steps WHERE id = fmea_rows.flow_step_id
                ) WHERE flow_step_name_snapshot IS NULL AND flow_step_id IS NOT NULL
                AND EXISTS (SELECT 1 FROM process_flow_steps WHERE id = fmea_rows.flow_step_id)
            """))
