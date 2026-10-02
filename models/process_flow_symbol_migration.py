"""승인된 기본 공정 기호 6종을 기존 공통코드에 누락된 경우만 등록합니다."""
import json
from datetime import datetime
from sqlalchemy import text

# 제조 공정 도시의 기본 기호. 고객 품번/공정번호/공정명 예시는 등록하지 않습니다.
INITIAL_SYMBOLS = (
    ("OPERATION", "가공", "CIRCLE"),
    ("TRANSPORT", "운반", "ARROW"),
    ("QUANTITY_INSPECTION", "수량검사", "SQUARE"),
    ("QUALITY_INSPECTION", "품질검사", "DIAMOND"),
    ("STORAGE", "저장", "INVERTED_TRIANGLE"),
    ("DELAY", "지체", "DELAY"),
)


def ensure_process_flow_symbol_codes(engine):
    with engine.begin() as connection:
        existing = set(connection.execute(text(
            "SELECT code FROM common_codes WHERE group_code=:group"
        ), {"group": "PROCESS_FLOW_SYMBOL"}).scalars())
        for order, (code, name, shape) in enumerate(INITIAL_SYMBOLS, 1):
            if code in existing:
                continue
            connection.execute(text(
                "INSERT INTO common_codes (group_code,group_name,code,code_name,sort_order,is_active,note,created_at) "
                "VALUES (:group,:group_name,:code,:name,:sort,'Y',:note,:created)"
            ), dict(group="PROCESS_FLOW_SYMBOL", group_name="공정흐름도 기호", code=code,
                    name=name, sort=order, note=json.dumps({"shape": shape}),
                    created=datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
