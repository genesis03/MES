"""작업일보 변경점 발생 유형범례의 기본 공통코드."""
from datetime import datetime

from sqlalchemy import text


GROUP_CODE = "PRODUCTION_CHANGE_TYPE"
INITIAL_TYPES = (
    ("1", "품번교체"),
    ("2", "툴교환"),
    ("3", "작업준비"),
    ("4", "자재불량"),
    ("5", "OFF-SET"),
    ("6", "교육"),
    ("7", "청소"),
    ("8", "계획정지"),
    ("9", "설비고장"),
    ("10", "기타"),
)


def ensure_production_change_type_codes(engine):
    with engine.begin() as connection:
        existing = set(connection.execute(text(
            "SELECT code FROM common_codes WHERE group_code=:group"
        ), {"group": GROUP_CODE}).scalars())
        for order, (code, name) in enumerate(INITIAL_TYPES, 1):
            if code in existing:
                continue
            connection.execute(text(
                "INSERT INTO common_codes (group_code,group_name,code,code_name,sort_order,is_active,note,created_at) "
                "VALUES (:group,:group_name,:code,:name,:sort,'Y',:note,:created)"
            ), dict(group=GROUP_CODE, group_name="생산 변경점 유형", code=code,
                    name=name, sort=order, note="작업일보 변경점 발생 유형범례",
                    created=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
