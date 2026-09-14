from sqlalchemy import inspect, text


def ensure_process_code_references(engine):
    """공정 참조값을 공정명 대신 process_code 기준으로 정규화합니다.

    과거 화면에서 품목 공정 등 일부 값이 process_name으로 저장된 경우가 있어,
    서버 시작 시 공정마스터의 code/name 매핑을 기준으로 안전하게 코드값으로 변환합니다.
    """
    with engine.begin() as connection:
        inspector = inspect(connection)
        table_names = set(inspector.get_table_names())
        if "processes" not in table_names:
            return

        mappings = connection.execute(
            text("SELECT process_code, process_name FROM processes")
        ).fetchall()
        mappings = [
            (str(row[0]).strip(), str(row[1]).strip())
            for row in mappings
            if row[0] and row[1] and str(row[0]).strip() != str(row[1]).strip()
        ]
        if not mappings:
            return

        for table_name in sorted(table_names):
            if table_name == "processes":
                continue
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            target_columns = []
            if "process_code" in columns:
                target_columns.append("process_code")
            if table_name == "item_master" and "production_loc" in columns:
                target_columns.append("production_loc")

            for column_name in target_columns:
                for process_code, process_name in mappings:
                    connection.execute(
                        text(
                            f'UPDATE "{table_name}" '
                            f'SET "{column_name}" = :process_code '
                            f'WHERE "{column_name}" = :process_name'
                        ),
                        {
                            "process_code": process_code,
                            "process_name": process_name,
                        },
                    )
