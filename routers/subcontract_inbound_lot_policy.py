from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.production_lot import ProductionLotModel
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundMaster
from routers.subcontract_inbound import (
    InboundCreateInput,
    _next_inbound_no,
    _received_qty,
    _serialize,
    _validate_storage,
)
from services.lot_service import next_lot_no

router = APIRouter(prefix="/api/subcontract/inbound", tags=["Subcontract Inbound Lot Policy"])


def _outsource_lot_prefix(outbound: SubcontractOutboundMaster) -> str:
    """외주가공 완료 LOT Prefix.

    LZ: 은도금 전용
    LC: 당사 반제품의 외주 CNC/추가가공
    """
    process_name = str(outbound.processing_type_name or "").replace(" ", "")
    if "은도금" in process_name:
        return "LZ"
    return "LC"


@router.post("")
def create_inbound_with_lot_policy(
    payload: InboundCreateInput,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    outbound = db.get(SubcontractOutboundMaster, payload.outbound_id)
    if outbound is None:
        raise HTTPException(404, "외주가공 출고 내역을 찾을 수 없습니다.")
    if outbound.status != "OUTBOUND":
        raise HTTPException(409, "출고완료 상태의 외주가공 건만 입고할 수 있습니다.")
    if not outbound.items:
        raise HTTPException(422, "입고할 외주가공 출고 품목이 없습니다.")
    _validate_storage(db, payload.storage_location)

    lot_by_id = {lot.id: (item, lot) for item in outbound.items for lot in item.lots}
    seen = set()
    validated = []
    for row in payload.lot_results:
        if row.outbound_lot_id in seen:
            raise HTTPException(422, "동일한 출고 LOT를 한 번의 입고에 중복 입력할 수 없습니다.")
        seen.add(row.outbound_lot_id)
        source = lot_by_id.get(row.outbound_lot_id)
        if source is None:
            raise HTTPException(422, "선택한 외주 출고에 포함되지 않은 LOT가 있습니다.")
        source_item, source_lot = source
        allocated = Decimal(str(source_lot.outbound_qty or 0))
        received = Decimal(str(_received_qty(db, source_lot.id)))
        remaining = allocated - received
        inbound_qty = Decimal(str(row.inbound_qty))
        sample_qty = Decimal(str(row.sample_qty))
        if remaining <= 0:
            raise HTTPException(409, f"{source_lot.lot_no}는 이미 전량 입고되었습니다.")
        if inbound_qty > remaining:
            raise HTTPException(422, f"{source_lot.lot_no}의 입고수량은 잔여수량 {float(remaining):g}을 초과할 수 없습니다.")
        if sample_qty > inbound_qty:
            raise HTTPException(422, f"{source_lot.lot_no}의 샘플수량은 금회 입고수량을 초과할 수 없습니다.")
        if not (row.supplier_lot_no or "").strip():
            raise HTTPException(422, f"{source_lot.lot_no}의 공급사 외주 LOT를 입력해 주세요.")
        validated.append((row, source_item, source_lot, allocated, received, inbound_qty))

    inbound_no = _next_inbound_no(db, payload.inbound_date)
    master = SubcontractInboundMaster(
        inbound_no=inbound_no,
        inbound_date=payload.inbound_date,
        outbound_id=outbound.id,
        outbound_no=outbound.outbound_no,
        order_id=outbound.order_id,
        order_no=outbound.order_no,
        partner_id=outbound.partner_id,
        partner_name=outbound.partner_name,
        processing_type_code=outbound.processing_type_code,
        processing_type_name=outbound.processing_type_name,
        storage_location=payload.storage_location,
        manager_name=outbound.manager_name,
        status="RECEIVED",
        note=payload.note or outbound.note,
        created_by=getattr(current_user, "username", None),
    )

    item_rows = {}
    reserved_lots = set()
    split_prefix = _outsource_lot_prefix(outbound)

    for row, source_item, source_lot, allocated, received, inbound_qty in validated:
        inbound_item = item_rows.get(source_item.id)
        if inbound_item is None:
            inbound_item = SubcontractInboundItem(
                outbound_item_id=source_item.id,
                order_item_id=source_item.order_item_id,
                previous_item_id=source_item.previous_item_id,
                item_id=source_item.item_id,
                previous_part_no=source_item.previous_part_no,
                part_no=source_item.order_part_no,
                part_name=source_item.order_part_name,
                spec=source_item.spec,
                unit=source_item.unit,
                outbound_qty=0,
                good_qty=0,
                defect_qty=0,
                note=source_item.note,
            )
            item_rows[source_item.id] = inbound_item
            master.items.append(inbound_item)


        # 첫 입고가 전량이면 기존 LOT를 유지합니다.
        # 부분입고/분할입고일 때만 공정별 신규 LOT를 발번합니다.
        is_split = received > 0 or inbound_qty < allocated
        if is_split:
            child_lot_no = next_lot_no(db, split_prefix, payload.inbound_date, 1, reserved_lots)
            reserved_lots.add(child_lot_no)
            db.add(LotRelationModel(
                parent_lot_no=source_lot.lot_no,
                child_lot_no=child_lot_no,
                process_code=outbound.processing_type_code,
                consumed_qty=float(inbound_qty),
            ))
            db.add(ProductionLotModel(
                lot_no=child_lot_no,
                item_id=source_item.item_id,
                part_no=source_item.order_part_no,
                lot_qty=float(inbound_qty),
                storage_location=payload.storage_location,
                status="ACTIVE",
                note=f"외주가공 부분입고 {inbound_no} / 원LOT {source_lot.lot_no}",
            ))
        else:
            child_lot_no = source_lot.lot_no
            stock = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == child_lot_no).one_or_none()
            if stock is None:
                db.add(ProductionLotModel(
                    lot_no=child_lot_no,
                    item_id=source_item.item_id,
                    part_no=source_item.order_part_no,
                    lot_qty=float(inbound_qty),
                    storage_location=payload.storage_location,
                    status="ACTIVE",
                    note=f"외주가공 전량입고 {inbound_no} / LOT 유지",
                ))
            else:
                stock.item_id = source_item.item_id
                stock.part_no = source_item.order_part_no
                stock.lot_qty = float(inbound_qty)
                stock.storage_location = payload.storage_location
                stock.status = "ACTIVE"
                stock.note = f"외주가공 전량입고 {inbound_no} / LOT 유지"

        inbound_item.lots.append(SubcontractInboundLot(
            outbound_lot_id=source_lot.id,
            source_lot_no=source_lot.lot_no,
            source_qty=float(allocated),
            good_qty=float(inbound_qty),
            defect_qty=0,
            defect_type=None,
            child_lot_no=child_lot_no,
            supplier_lot_no=(row.supplier_lot_no or "").strip() or None,
            sample_qty=float(row.sample_qty),
        ))
        inbound_item.outbound_qty += float(inbound_qty)
        inbound_item.good_qty += float(inbound_qty)

    db.add(master)
    db.commit()
    db.refresh(master)
    return _serialize(master)
