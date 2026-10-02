"""기존 FMEA 내용은 보존하고 연결/비교용 nullable 컬럼만 추가합니다."""
from sqlalchemy import inspect, text

COLUMNS = {
    "fmea_revisions": {
        "flow_revision_id": "INTEGER REFERENCES process_flow_revisions(id)",
        "diff_tracking": "BOOLEAN NOT NULL DEFAULT FALSE",
        "vehicle_model_snapshot": "VARCHAR(100)",
        "process_owner": "VARCHAR(100)",
        "completion_due_date": "DATE",
        "mass_production_date": "DATE",
    },
    "fmea_rows": {
        "flow_step_id": "INTEGER REFERENCES process_flow_steps(id)",
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
    # 기존 Revision·분석행·점수·공정코드·등록일의 자동 백필/재연결은 하지 않습니다.
