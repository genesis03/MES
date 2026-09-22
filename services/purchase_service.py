"""Purchase writes must go through these transaction-owning services."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import math

from fastapi import HTTPException
from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from models.models import (ItemMasterModel, PurchaseOrderMaster, PurchaseOrderItem,
                           PurchaseInboundMaster, PurchaseInboundItem, WarehouseMasterModel, StorageLocationModel)
from models.partner import Partner


@contextmanager
def purchase_transaction(db):
    if db.in_transaction():
        raise RuntimeError("구매 저장에는 새 세션이 필요합니다.")
    try:
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            db.execute(text("PRAGMA busy_timeout=30000"))
            db.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
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


def validate_storage_master(db, items):
    warehouse_codes = {item.warehouse_code for item in items if item.warehouse_code}
    location_codes = {item.storage_location for item in items}
    warehouses = set()
    if warehouse_codes:
        warehouses = set(db.scalars(select(WarehouseMasterModel.warehouse_code).where(
            WarehouseMasterModel.warehouse_code.in_(warehouse_codes), WarehouseMasterModel.is_active == "Y")))
    locations = set(db.scalars(select(StorageLocationModel.location_code).where(
        StorageLocationModel.location_code.in_(location_codes), StorageLocationModel.is_active == "Y")))
    if warehouses != warehouse_codes:
        raise HTTPException(422, "선택한 입고창고가 등록된 활성 창고가 아닙니다.")
    if locations != location_codes:
        raise HTTPException(422, "등록된 활성 저장위치를 선택하세요.")


def validate_vendor(db, payload):
    partner = db.get(Partner, payload.partner_id)
    if partner is None:
        raise HTTPException(404, "거래처를 찾을 수 없습니다.")
    if partner.is_active != "Y" or partner.partner_type not in ("VENDOR", "BOTH"):
        raise HTTPException(422, "활성 공급사 거래처만 발주할 수 있습니다.")


def create_order(db, payload, created_by):
    from schemas.purchase import OrderOut
    with purchase_transaction(db):
        validate_master_data(db, payload)
        validate_vendor(db, payload)
        validate_storage_master(db, payload.items)
        master = PurchaseOrderMaster(**payload.model_dump(exclude={"items", "created_by"}), created_by=created_by,
                                     po_no=next_number(db, PurchaseOrderMaster.po_no, "PO"))
        parts = {part.part_no: part for part in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({item.part_no for item in payload.items})
        )}
        master.items = [
            PurchaseOrderItem(
                **item.model_dump(),
                item_id=parts[item.part_no].id,
                unit=parts[item.part_no].unit,
            )
            for item in payload.items
        ]
        db.add(master)
        db.flush()
        result = OrderOut.model_validate(master)
    return result


def update_order(db, po_id, payload):
    from schemas.purchase import OrderOut
    with purchase_transaction(db):
        master = db.get(PurchaseOrderMaster, po_id)
        if master is None:
            raise HTTPException(404, "발주를 찾을 수 없습니다.")
        item_ids = [item.id for item in master.items]
        has_receipt = any((item.received_qty or 0) > 0 for item in master.items)
        if item_ids and not has_receipt:
            has_receipt = db.query(PurchaseInboundItem.id).filter(PurchaseInboundItem.po_item_id.in_(item_ids)).first() is not None
        if has_receipt:
            raise HTTPException(409, "입고 이력이 있는 발주는 직접 수정할 수 없습니다. 입고 조회에서 이력을 확인하세요.")
        if master.status == "CANCELLED":
            raise HTTPException(409, "취소된 발주는 수정할 수 없습니다.")
        validate_master_data(db, payload)
        validate_vendor(db, payload)
        validate_storage_master(db, payload.items)
        master.order_date = payload.order_date
        master.delivery_due_date = payload.delivery_due_date
        master.partner_id = payload.partner_id
        master.partner_name = payload.partner_name
        master.manager_name = payload.manager_name
        master.note = payload.note
        master.status = "ORDERED"
        parts = {part.part_no: part for part in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({item.part_no for item in payload.items})
        )}
        master.items = [
            PurchaseOrderItem(
                **item.model_dump(),
                item_id=parts[item.part_no].id,
                unit=parts[item.part_no].unit,
            )
            for item in payload.items
        ]
        db.flush()
        result = OrderOut.model_validate(master)
    return result


def linked_order_item(db, item, master):
    if item.po_item_id is None:
        return None
    po_item = db.get(PurchaseOrderItem, item.po_item_id)
    if po_item is None:
        raise HTTPException(404, f"발주 상세를 찾을 수 없습니다: {item.po_item_id}")
    order = po_item.order
    if order.status == "CANCELLED":
        raise HTTPException(409, "취소된 발주에는 입고할 수 없습니다.")
    if po_item.item_id and item.item_id:
        if po_item.item_id != item.item_id:
            raise HTTPException(422, "발주 품목과 입고 품목이 일치하지 않습니다.")
    elif po_item.part_no != item.part_no:
        raise HTTPException(422, "발주 품목과 입고 품목이 일치하지 않습니다.")
    if order.partner_id != master.partner_id or order.partner_name != master.partner_name:
        raise HTTPException(422, "발주 거래처와 입고 거래처가 일치하지 않습니다.")
    return po_item


def validate_inbound_storage(db, items):
    location_codes = {item.storage_location for item in items}
    locations = set(db.scalars(select(StorageLocationModel.location_code).where(
        StorageLocationModel.location_code.in_(location_codes), StorageLocationModel.is_active == "Y")))
    if locations != location_codes:
        raise HTTPException(422, "등록된 활성 저장위치를 선택하세요.")


def refresh_order_status(order):
    for item in order.items:
        item.status = "COMPLETED" if item.received_qty >= item.order_qty else ("PARTIAL" if item.received_qty > 0 else "WAITING")
    order.status = "COMPLETED" if all(i.received_qty >= i.order_qty for i in order.items) else ("PARTIAL" if any(i.received_qty > 0 for i in order.items) else "ORDERED")


def confirm_saved_inbound(db, master, preserve_lot=False):
    affected = {}
    for position, item in enumerate(master.items, start=1):
        po_item = linked_order_item(db, item, master)
        if po_item is not None:
            received = float(Decimal(str(po_item.received_qty)) + Decimal(str(item.inbound_qty)))
            if not math.isfinite(received):
                raise HTTPException(422, "누적 입고수량이 저장 가능한 범위를 초과했습니다.")
            if received > po_item.order_qty:
                raise HTTPException(422, f"입고수량이 발주수량을 초과합니다: {po_item.part_no}")
            po_item.received_qty = received
            affected[po_item.order.id] = po_item.order
        item.internal_lot_no = (item.internal_lot_no if preserve_lot else None) or f"LOT-{master.inbound_no}-{position:03d}"
    for order in affected.values():
        refresh_order_status(order)
    master.status = "CONFIRMED"


def inbound_values(item, unit, keep_internal=False):
    values = item.model_dump(exclude={"inbound_item_id"})
    values["unit"] = unit
    if not keep_internal:
        values["internal_lot_no"] = None
    return values


def create_inbound(db, payload, created_by, draft=False):
    from schemas.purchase import InboundOut
    with purchase_transaction(db):
        validate_master_data(db, payload)
        validate_inbound_storage(db, payload.items)
        master = PurchaseInboundMaster(**payload.model_dump(exclude={"items", "created_by"}), created_by=created_by,
                                       inbound_no=next_number(db, PurchaseInboundMaster.inbound_no, "IN"),
                                       status="DRAFT" if draft else "CONFIRMED")
        parts = {part.part_no: part for part in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({item.part_no for item in payload.items})
        )}
        for item in payload.items:
            if draft and item.po_item_id is None:
                raise HTTPException(422, "발주를 불러온 품목만 임시저장할 수 있습니다.")
            part = parts[item.part_no]
            row = PurchaseInboundItem(**inbound_values(item, part.unit, keep_internal=not draft))
            row.item_id = part.id
            linked_order_item(db, row, master)
            master.items.append(row)
        db.add(master)
        if not draft:
            confirm_saved_inbound(db, master, preserve_lot=True)
        db.flush()
        result = InboundOut.model_validate(master)
    return result


def confirm_inbound(db, inbound_id):
    from schemas.purchase import InboundOut
    with purchase_transaction(db):
        master = db.get(PurchaseInboundMaster, inbound_id)
        if master is None:
            raise HTTPException(404, "구매 입력을 찾을 수 없습니다.")
        if master.status != "DRAFT":
            raise HTTPException(409, "이미 입고 확정된 구매 입력입니다.")
        confirm_saved_inbound(db, master)
        db.flush()
        result = InboundOut.model_validate(master)
    return result


def update_inbound_draft(db, inbound_id, payload):
    return update_inbound(db, inbound_id, payload, allow_confirmed=False)


def update_inbound(db, inbound_id, payload, allow_confirmed=True):
    from schemas.purchase import InboundOut
    with purchase_transaction(db):
        master = db.get(PurchaseInboundMaster, inbound_id)
        if master is None:
            raise HTTPException(404, "구매 입력을 찾을 수 없습니다.")
        if master.status == "CONFIRMED" and not allow_confirmed:
            raise HTTPException(409, "확정된 입고는 이 경로에서 수정할 수 없습니다.")
        if master.status not in ("DRAFT", "CONFIRMED"):
            raise HTTPException(409, "수정할 수 없는 구매 상태입니다.")
        validate_master_data(db, payload)
        validate_inbound_storage(db, payload.items)
        if any(item.po_item_id is None for item in payload.items):
            raise HTTPException(422, "발주를 불러온 품목만 저장할 수 있습니다.")
        if master.partner_id != payload.partner_id or master.partner_name != payload.partner_name:
            raise HTTPException(422, "구매 확정 후에는 연결된 발주의 거래처를 변경할 수 없습니다.")

        master.inbound_date = payload.inbound_date
        master.invoice_no = payload.invoice_no
        master.note = payload.note
        parts = {part.part_no: part for part in db.query(ItemMasterModel).filter(
            ItemMasterModel.part_no.in_({item.part_no for item in payload.items})
        )}

        if master.status == "DRAFT":
            new_items = []
            for item in payload.items:
                part = parts[item.part_no]
                row = PurchaseInboundItem(**inbound_values(item, part.unit))
                row.item_id = part.id
                linked_order_item(db, row, master)
                new_items.append(row)
            master.items = new_items
        else:
            existing = {row.id: row for row in master.items}
            incoming_ids = {item.inbound_item_id for item in payload.items}
            if None in incoming_ids or incoming_ids != set(existing):
                raise HTTPException(409, "확정된 구매는 LOT 행 추가/삭제 없이 기존 행만 정정할 수 있습니다.")

            affected = {}
            for row in master.items:
                po_item = db.get(PurchaseOrderItem, row.po_item_id) if row.po_item_id else None
                if po_item is not None:
                    po_item.received_qty = max(0.0, float(Decimal(str(po_item.received_qty)) - Decimal(str(row.inbound_qty))))
                    affected[po_item.order.id] = po_item.order

            for item in payload.items:
                row = existing[item.inbound_item_id]
                part = parts[item.part_no]
                if row.po_item_id != item.po_item_id or (row.item_id and row.item_id != part.id):
                    raise HTTPException(409, "확정된 구매에서는 연결 발주/품목을 변경할 수 없습니다.")
                row.item_id = part.id
                row.part_no = part.part_no
                po_item = db.get(PurchaseOrderItem, row.po_item_id) if row.po_item_id else None
                if po_item is None:
                    raise HTTPException(404, "연결된 발주 상세를 찾을 수 없습니다.")
                new_received = float(Decimal(str(po_item.received_qty)) + Decimal(str(item.inbound_qty)))
                if new_received > po_item.order_qty:
                    raise HTTPException(422, f"정정 후 입고수량이 발주수량을 초과합니다: {row.part_no}")
                po_item.received_qty = new_received
                affected[po_item.order.id] = po_item.order
                row.inbound_qty = item.inbound_qty
                row.supplier_lot_no = item.supplier_lot_no
                if item.warehouse_code is not None:
                    row.warehouse_code = item.warehouse_code
                row.storage_location = item.storage_location
                row.note = item.note
            for order in affected.values():
                refresh_order_status(order)

        db.flush()
        result = InboundOut.model_validate(master)
    return result