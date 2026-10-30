"""FIFO checks for subcontract dispatch and receipt; never rewrite historical LOT links."""
import re
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func

from models.lot_relation import LotRelationModel
from models.models import PurchaseInboundItem, PurchaseInboundMaster
from models.production import ProductionPerformance
from models.production_lot import ProductionLotModel
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundItem, SubcontractOutboundLot, SubcontractOutboundMaster
from services.production_lot_service import performance_id_from_lot_note


def _source_date(db, lot_no, seen=None):
    seen = set() if seen is None else seen
    if lot_no in seen:
        raise HTTPException(409, f"LOT 연결에 순환이 있어 생산일을 확인할 수 없습니다: {lot_no}")
    seen.add(lot_no)
    stock = db.query(ProductionLotModel).filter_by(lot_no=lot_no).first()
    if stock:
        performance_id = performance_id_from_lot_note(stock.note)
        performance = db.get(ProductionPerformance, performance_id) if performance_id else None
        if performance:
            return performance.performance_date
        parents = db.query(LotRelationModel.parent_lot_no).filter_by(child_lot_no=lot_no).all()
        if parents:
            return min(_source_date(db, parent[0], set(seen)) for parent in parents)
        # Native production LOTs encode their production date, including historical imports.
        match = re.match(r'^L[A-Z](\d{8})\d+$', lot_no)
        if match:
            try:
                return datetime.strptime(match[1], '%Y%m%d').date().isoformat()
            except ValueError:
                pass
        raise HTTPException(409, f"생산일을 확인할 수 없는 LOT입니다. 생산실적 연결을 확인하세요: {lot_no}")
    purchase = (db.query(PurchaseInboundMaster.inbound_date)
                .join(PurchaseInboundItem, PurchaseInboundItem.inbound_id == PurchaseInboundMaster.id)
                .filter(PurchaseInboundItem.internal_lot_no == lot_no).first())
    if purchase:
        return purchase[0]
    raise HTTPException(409, f"LOT의 생산일/입고일을 확인할 수 없습니다: {lot_no}")


def sort_source_lots(db, rows):
    """Production date first; purchase material uses receipt date. LOT number breaks ties."""
    dated = [(row, _source_date(db, row['lot_no'])) for row in rows]
    dated.sort(key=lambda pair: (pair[1], pair[0]['lot_no']))
    return [dict(row, fifo_date=day) for row, day in dated]


def validate_outbound_selection(available, selected):
    selected = set(selected)
    expected = [row['lot_no'] for row in available[:len(selected)]]
    if selected != set(expected):
        first = next((lot for lot in expected if lot not in selected), '선행 LOT')
        raise HTTPException(409, f"외주 출고는 생산일 순서로 배정해야 합니다. {first}부터 선택하세요.")


def validate_inbound_selection(db, outbound, selected, exclude_inbound_id=None):
    """Selected quantities must form a FIFO prefix across same partner/part/process dispatches."""
    received = (db.query(SubcontractInboundLot.outbound_lot_id,
                         func.sum(SubcontractInboundLot.good_qty))
                .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
                .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
                .filter(SubcontractInboundMaster.status == 'RECEIVED'))
    if exclude_inbound_id is not None:
        received = received.filter(SubcontractInboundMaster.id != exclude_inbound_id)
    received = dict(received.group_by(SubcontractInboundLot.outbound_lot_id).all())
    candidates = (db.query(SubcontractOutboundMaster, SubcontractOutboundItem, SubcontractOutboundLot)
                  .join(SubcontractOutboundItem, SubcontractOutboundItem.outbound_id == SubcontractOutboundMaster.id)
                  .join(SubcontractOutboundLot, SubcontractOutboundLot.outbound_item_id == SubcontractOutboundItem.id)
                  .filter(SubcontractOutboundMaster.status == 'OUTBOUND',
                          SubcontractOutboundMaster.partner_id == outbound.partner_id,
                          SubcontractOutboundMaster.processing_type_code == outbound.processing_type_code)
                  .order_by(SubcontractOutboundMaster.outbound_date, SubcontractOutboundMaster.id,
                            SubcontractOutboundLot.id).all())
    selected = {key: Decimal(str(value)) for key, value in selected.items()}
    selected_items = {item.id: item for item in outbound.items for lot in item.lots if lot.id in selected}
    groups = {(item.previous_item_id, item.item_id) for item in selected_items.values()}
    for previous_id, item_id in groups:
        rows = [(master, lot) for master, item, lot in candidates
                if item.previous_item_id == previous_id and item.item_id == item_id]
        total = sum((selected.get(lot.id, Decimal(0)) for _, lot in rows), Decimal(0))
        for master, lot in rows:
            available = max(Decimal(str(lot.outbound_qty)) - Decimal(str(received.get(lot.id, 0))), Decimal(0))
            expected = min(total, available)
            if abs(selected.get(lot.id, Decimal(0)) - expected) > Decimal('0.000000001'):
                raise HTTPException(409, f"외주 입고는 먼저 출고한 미입고 LOT부터 처리해야 합니다. 출고 {master.outbound_no} / LOT {lot.lot_no}부터 입고하세요.")
            total -= expected


def validate_receipt_reduction(db, master, new_quantities):
    """Do not reopen an earlier LOT while a later dispatch LOT already has receipts."""
    outbound = db.get(SubcontractOutboundMaster, master.outbound_id)
    if outbound is None:
        raise HTTPException(409, "원출고 정보를 찾을 수 없습니다.")
    for item in master.items:
        for lot in item.lots:
            if Decimal(str(new_quantities.get(lot.id, 0))) >= Decimal(str(lot.good_qty or 0)):
                continue
            source = db.get(SubcontractOutboundLot, lot.outbound_lot_id)
            if source is None:
                raise HTTPException(409, "원출고 LOT 정보를 찾을 수 없습니다.")
            current_key = (outbound.outbound_date, outbound.id, source.id)
            later = (db.query(SubcontractOutboundMaster, SubcontractOutboundLot)
                     .join(SubcontractOutboundItem, SubcontractOutboundItem.outbound_id == SubcontractOutboundMaster.id)
                     .join(SubcontractOutboundLot, SubcontractOutboundLot.outbound_item_id == SubcontractOutboundItem.id)
                     .join(SubcontractInboundLot, SubcontractInboundLot.outbound_lot_id == SubcontractOutboundLot.id)
                     .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
                     .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
                     .filter(SubcontractOutboundMaster.status == 'OUTBOUND',
                             SubcontractOutboundMaster.partner_id == master.partner_id,
                             SubcontractOutboundMaster.processing_type_code == master.processing_type_code,
                             SubcontractOutboundItem.previous_item_id == item.previous_item_id,
                             SubcontractOutboundItem.item_id == item.item_id,
                             SubcontractInboundMaster.status == 'RECEIVED',
                             SubcontractInboundMaster.id != master.id,
                             SubcontractInboundLot.good_qty > 0).all())
            for dispatch, later_lot in later:
                if (dispatch.outbound_date, dispatch.id, later_lot.id) > current_key:
                    raise HTTPException(409, f"후순위 LOT {later_lot.lot_no}에 입고 이력이 있어 선행 LOT 수량을 줄이거나 취소할 수 없습니다. 후순위 입고부터 정정하세요.")
