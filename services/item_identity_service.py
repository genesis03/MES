import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from models.item_identity import ItemPartNoHistory
from models.item_identity_migration import IDENTITY_COLUMNS
from models.models import ItemMasterModel, ShippingMasterModel


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def rename_item_part_no(
    db: Session,
    item: ItemMasterModel,
    new_part_no: str,
    changed_by: str | None = None,
    reason: str | None = None,
) -> None:
    """영구 item_id는 유지하고 품번을 전체 업무 데이터에 일괄 반영합니다.

    기존 테이블은 아직 part_no 문자열을 조회에 사용하므로, 전환기간에는
    item_id 기준으로 연관 행을 찾아 part_no 호환 컬럼도 함께 갱신합니다.
    품번 변경 이력은 item_part_no_history에 별도로 보존합니다.
    """
    new_part_no = str(new_part_no or "").strip()
    old_part_no = str(item.part_no or "").strip()

    if not new_part_no:
        raise ValueError("품번은 비워둘 수 없습니다.")
    if new_part_no == old_part_no:
        return

    duplicate = (
        db.query(ItemMasterModel)
        .filter(ItemMasterModel.part_no == new_part_no, ItemMasterModel.id != item.id)
        .first()
    )
    if duplicate:
        raise ValueError(f"이미 등록된 품번입니다. ({new_part_no})")

    bind = db.get_bind()
    if bind.dialect.name != "sqlite":
        raise ValueError("품번 일괄 변경은 현재 SQLite 운영 DB에서만 지원합니다.")

    # 기존 part_no FK가 남아 있는 SQLite DB도 한 트랜잭션 안에서 안전하게 갱신합니다.
    db.execute(text("PRAGMA defer_foreign_keys = ON"))

    for table_name, id_column, part_column in IDENTITY_COLUMNS:
        table_q = _quote(table_name)
        id_q = _quote(id_column)
        part_q = _quote(part_column)

        # 존재하지 않는 테이블은 SQLite sqlite_master 검사로 건너뜁니다.
        exists = db.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name LIMIT 1"),
            {"name": table_name},
        ).scalar_one_or_none()
        if not exists:
            continue

        columns = {
            row[1]
            for row in db.execute(text(f"PRAGMA table_info({table_q})")).fetchall()
        }
        if id_column not in columns or part_column not in columns:
            continue

        db.execute(
            text(
                f"""
                UPDATE {table_q}
                   SET {part_q} = :new_part_no,
                       {id_q} = :item_id
                 WHERE {id_q} = :item_id
                    OR ({id_q} IS NULL AND {part_q} = :old_part_no)
                """
            ),
            {
                "new_part_no": new_part_no,
                "old_part_no": old_part_no,
                "item_id": item.id,
            },
        )

    # 레거시 출고 원장(shipping_master)은 JSON 스테이징 구조라 item_id 컬럼이 없습니다.
    # MES 출고에서 생성되었거나 과거 업로드된 행 중 품번(textBox9)이 정확히 이전 품번인 행만 함께 갱신합니다.
    for staging in db.query(ShippingMasterModel).all():
        try:
            row = json.loads(staging.row_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if str(row.get("textBox9") or "").strip() != old_part_no:
            continue
        row["textBox9"] = new_part_no
        barcode = str(row.get("barcode1") or "")
        old_prefix = f"P{old_part_no}Q"
        if barcode.startswith(old_prefix):
            row["barcode1"] = f"P{new_part_no}Q" + barcode[len(old_prefix):]
        staging.row_json = json.dumps(row, ensure_ascii=False)

    item.part_no = new_part_no
    db.add(
        ItemPartNoHistory(
            item_id=item.id,
            old_part_no=old_part_no,
            new_part_no=new_part_no,
            changed_by=(str(changed_by).strip() if changed_by else None),
            reason=(str(reason).strip() if reason else None),
        )
    )
