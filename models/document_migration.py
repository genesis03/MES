"""기존 공통코드를 재사용하며 누락된 문서 분류만 추가합니다."""
from datetime import datetime

from sqlalchemy import inspect, text

DRAWING_TYPE = "DRAWING"
DOCUMENT_TYPES = (
    (DRAWING_TYPE, "도면"),
    ("WORK_STANDARD", "작업표준서"),
    ("INSPECTION_STANDARD", "검사기준서"),
    ("TECHNICAL", "기타 기술문서"),
)


def ensure_document_codes(engine):
    with engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("item_revisions"):
            columns = {column["name"] for column in inspector.get_columns("item_revisions")}
            if "eco_no" not in columns:
                connection.execute(text('ALTER TABLE "item_revisions" ADD COLUMN "eco_no" VARCHAR(100)'))
    groups = (
        ("DOCUMENT_TYPE", "기술문서 종류", DOCUMENT_TYPES),
        ("DOCUMENT_FILE_ROLE", "기술문서 파일 역할", (("VIEW", "열람용"), ("SOURCE", "원본"))),
    )
    with engine.begin() as connection:
        for group_code, group_name, rows in groups:
            existing = set(connection.execute(text(
                "SELECT code FROM common_codes WHERE group_code=:group"
            ), {"group": group_code}).scalars())
            for order, (code, name) in enumerate(rows, 1):
                if code in existing:
                    continue
                connection.execute(text(
                    "INSERT INTO common_codes (group_code,group_name,code,code_name,sort_order,is_active,created_at) "
                    "VALUES (:group,:group_name,:code,:name,:sort,'Y',:created)"
                ), dict(group=group_code, group_name=group_name, code=code, name=name,
                        sort=order, created=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
