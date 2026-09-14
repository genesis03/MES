from datetime import datetime

from sqlalchemy import inspect, text


DEFECT_TYPE_SEED = (
    ("01", "찍힘"),
    ("02", "긁힘"),
    ("03", "치수"),
    ("04", "칩"),
    ("05", "버"),
    ("06", "소재"),
    ("07", "오조립"),
    ("08", "미도금"),
    ("09", "얼룩"),
    ("10", "변색"),
    ("11", "조도"),
    ("12", "형상"),
)


def ensure_quality_master_data(engine):
    """품질 입고검사에서 사용하는 표준 불량유형을 공통코드 마스터에 보장합니다."""
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "common_codes" not in inspector.get_table_names():
            return

        existing = {
            row[0]
            for row in connection.execute(
                text("SELECT code FROM common_codes WHERE group_code = :group_code"),
                {"group_code": "DEFECT_TYPE"},
            ).fetchall()
        }
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for sort_order, (code, code_name) in enumerate(DEFECT_TYPE_SEED, start=1):
            if code in existing:
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO common_codes (
                        group_code, group_name, code, code_name,
                        sort_order, is_active, note, created_at
                    ) VALUES (
                        :group_code, :group_name, :code, :code_name,
                        :sort_order, 'Y', :note, :created_at
                    )
                    """
                ),
                {
                    "group_code": "DEFECT_TYPE",
                    "group_name": "불량유형",
                    "code": code,
                    "code_name": code_name,
                    "sort_order": sort_order,
                    "note": "입고 품목 불량 처리 표준 분류",
                    "created_at": now_str,
                },
            )
