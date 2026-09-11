"""Purchase writes must go through these transaction-owning services."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import math

from fastapi import HTTPException
from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from models.models import ItemMasterModel, PurchaseOrderMaster, PurchaseOrderItem, PurchaseInboundMaster, PurchaseInboundItem
from models.partner import Partner


@contextmanager
def purchase_transaction(db):
    # A dedicated fresh session avoids sharing the authentication read transaction.
    if db.in_transaction():
        raise RuntimeError("구매 저장에는 새 세션이 필요합니다.")
    try:
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            db.execute(text("PRAGMA busy_timeout=30000"))
            db.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
            # Serialize numbering + receipt updates across workers, as on SQLite.
            db.execute(text("SELECT pg_advisory_xact_lock(70611701)"))
        else:
            raise HTTPException(501, "SQLite 또는 PostgreSQL만 지원합니다.")
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "참조 데이터 또는 전표번호가 충돌했습니다.") from exc
    except OperationalError as exc:
        db.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise HTTPException(503, "다른 입고/발주 처리가 진행 중입니다. 잠시 후 다시 시도하세요.", headers={"Retry-After": "1"}) from exc
        raise
    except Exception:
        db.rollback()
        raise


def next_number(db, column, kind):
    # Numeric MAX, not text ordering: 1000 must follow 999.
    prefix = f"{kind}-{datetime.now():%Y%m%d}-"
    sequence = db.scalar(select(func.max(cast(func.substr(column, len(prefix) + 1), Integer))).where(column.startswith(prefix))) or 0
    number = f"{prefix}{sequence + 1:03d}"
    if len(number) > 30:
        raise HTTPException(409, "일일 전표번호 범위를 초과했습니다.")
    return number


def validate_master_data(db, payload):
    if payload.partner_id is not None:
        partner = db.get(Partner, payload.partner_id)
        if partner is None:
            raise HTTPException(404, "거래처를 찾을 수 없습니다.")
        if partner.partner_name != payload.partner_name:
            raise HTTPException(422, "거래처 ID와 거래처명이 일치하지 않습니다.")
    parts = {item.part_no for item in payload.items}
    existing = set(db.scalars(select(ItemMasterModel.part_no).where(ItemMasterModel.part_no.in_(parts))))
    missing = sorted(parts - existing)
    if missing:
        raise HTTPException(404, f"등록되지 않은 품목입니다: {', '.join(missing)}")


def create_order(db, payload, created_by):
    from schemas.purchase import OrderOut
    with purchase_transaction(db):
        validate_master_data(db, payload)
        master = PurchaseOrderMaster(**payload.model_dump(exclude={"items", "created_by"}), created_by=created_by,
                                     po_no=next_number(db, PurchaseOrderMaster.po_no, "PO"))
        master.items = [PurchaseOrderItem(**item.model_dump()) for item in payload.items]
        db.add(master)
        db.flush()
        result = OrderOut.model_validate(master)
    return result


def create_inbound(db, payload, created_by):
    from schemas.purchase import InboundOut
    with purchase_transaction(db):
        validate_master_data(db, payload)
        master = PurchaseInboundMaster(**payload.model_dump(exclude={"items", "created_by"}), created_by=created_by,
                                       inbound_no=next_number(db, PurchaseInboundMaster.inbound_no, "IN"))
        affected = {}
        for position, item in enumerate(payload.items, start=1):
            if item.po_item_id is not None:
                po_item = db.get(PurchaseOrderItem, item.po_item_id)
                if po_item is None:
                    raise HTTPException(404, f"발주 상세를 찾을 수 없습니다: {item.po_item_id}")
                order = po_item.order
                if order.status == "CANCELLED":
                    raise HTTPException(409, "취소된 발주에는 입고할 수 없습니다.")
                if po_item.part_no != item.part_no:
                    raise HTTPException(422, "발주 품목과 입고 품목이 일치하지 않습니다.")
                if order.partner_id != payload.partner_id or order.partner_name != payload.partner_name:
                    raise HTTPException(422, "발주 거래처와 입고 거래처가 일치하지 않습니다.")
                received = float(Decimal(str(po_item.received_qty)) + Decimal(str(item.inbound_qty)))
                if not math.isfinite(received):
                    raise HTTPException(422, "누적 입고수량이 저장 가능한 범위를 초과했습니다.")
                po_item.received_qty = received
                po_item.status = "COMPLETED" if received >= po_item.order_qty else "PARTIAL"
                affected[order.id] = order
            values = item.model_dump()
            values["internal_lot_no"] = item.internal_lot_no or f"LOT-{master.inbound_no}-{position:03d}"
            master.items.append(PurchaseInboundItem(**values))
        for order in affected.values():
            order.status = "COMPLETED" if all(i.received_qty >= i.order_qty for i in order.items) else "PARTIAL"
        db.add(master)
        db.flush()
        result = InboundOut.model_validate(master)
    return result
