"""Dated evidence for the inventory reconciliation sheet, without combining sources."""
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from fastapi import HTTPException
from models.models import PurchaseInboundMaster, PurchaseInboundItem
from models.production import ProductionPerformance
from models.lot_consumption import LotConsumptionModel
from models.production_lot import ProductionLotModel
from models.packing import PackingMaster
from models.subcontract_outbound import SubcontractOutboundMaster
from models.subcontract_inbound import SubcontractInboundMaster
from models.sales import ShipmentMaster
from models.production_defect import QualityProductionDefect
from models.inventory_adjustment import InventoryAdjustmentModel
from models.production_sync import ExternalProductionRecord
from models.packing_sync import ExternalPackingRecord, PackingSourcePresence
from models.subcontract_fault_sync import ExternalSubcontractFault
from models.shipment_sync import ExternalShipment, ExternalShipmentLot
from models.inventory_ledger import InventoryLedgerEntry
from services.production_sync_mapping import ItemConnections


def number(value):
    try:result = Decimal(str(value or 0))
    except InvalidOperation:raise HTTPException(409,'집계 원본에 숫자가 아닌 수불수량이 있습니다. 원본을 확인하세요.')
    if not result.is_finite():
        raise HTTPException(409,'집계 원본의 수불수량이 유효하지 않습니다.')
    return result


def events_for_month(db, first, last, source):
    result = []
    warnings = []
    connections = ItemConnections(db)
    lots = {lot.lot_no: lot for lot in db.query(ProductionLotModel)}

    def add(day, item_id, category, direction, qty, document='', lot='', note='', raw_part='', process='', entry_id=None):
        qty = number(qty)
        if qty <= 0 or not first <= day <= last:
            return
        result.append(dict(date=day, item_id=item_id, raw_part=raw_part, process=process,
                           category=category, direction=direction, qty=qty,
                           document_no=document, lot_no=lot, note=note, entry_id=entry_id))

    def external(day, part, process, category, direction, qty, document='', lot='', note=''):
        item, _ = connections.resolve(part, process)
        add(day, item.id if item else None, category, direction, qty, document, lot, note,
            raw_part=part if not item else '', process=process if not item else '')

    if source == 'mes':
        for master, row in (db.query(PurchaseInboundMaster, PurchaseInboundItem)
                            .join(PurchaseInboundItem, PurchaseInboundItem.inbound_id == PurchaseInboundMaster.id)
                            .filter(PurchaseInboundMaster.status == 'CONFIRMED', PurchaseInboundMaster.inbound_date.between(first, last))):
            add(master.inbound_date, row.item_id, '구매입고', 'IN', row.inbound_qty, master.inbound_no, row.internal_lot_no or '')
        performances = db.query(ProductionPerformance).filter(ProductionPerformance.performance_date.between(first, last)).all()
        perf_by_id = {r.id:r for r in performances}
        for row in performances:
            item_id = row.work_order.item_id
            produced = [lot.lot_no for lot in lots.values() if (lot.note or '').split('|')[0] == f'PERF:{row.id}']
            lot = ', '.join(produced)
            doc = row.work_order.work_order_no
            add(row.performance_date, item_id, '생산', 'IN', number(row.good_qty)+number(row.defect_qty)+number(row.setup_qty), doc, lot)
            add(row.performance_date, item_id, '생산불량', 'OUT', row.defect_qty, doc, lot)
            add(row.performance_date, item_id, 'SET-UP', 'OUT', row.setup_qty, doc, lot)
        for row in db.query(LotConsumptionModel).filter(LotConsumptionModel.performance_id.in_(list(perf_by_id))):
            perf = perf_by_id[row.performance_id]
            add(perf.performance_date, row.item_id, '공정투입', 'OUT', row.consumed_qty, perf.work_order.work_order_no, row.lot_no)
        for master in db.query(SubcontractOutboundMaster).filter(SubcontractOutboundMaster.status == 'OUTBOUND', SubcontractOutboundMaster.outbound_date.between(first, last)):
            for item in master.items:
                for lot in item.lots:
                    add(master.outbound_date, item.previous_item_id, '외주출고', 'OUT', lot.outbound_qty, master.outbound_no, lot.lot_no)
        for master in db.query(SubcontractInboundMaster).filter(SubcontractInboundMaster.status == 'RECEIVED', SubcontractInboundMaster.inbound_date.between(first, last)):
            for item in master.items:
                for lot in item.lots:
                    add(master.inbound_date, item.item_id, '외주입고', 'IN', lot.good_qty, master.inbound_no, lot.child_lot_no or lot.source_lot_no)
        for master in db.query(PackingMaster).filter(PackingMaster.status == 'PACKED', PackingMaster.packing_date.between(first, last)):
            for allocation in master.allocations:
                lot = lots.get(allocation.source_lot_no)
                if lot:
                    add(master.packing_date, lot.item_id, '포장투입', 'OUT', allocation.allocated_qty, master.packing_no, lot.lot_no)
                else:
                    warnings.append(f'포장 {master.packing_no}: 투입 LOT {allocation.source_lot_no}의 품번을 확인할 수 없습니다.')
            for box in master.boxes:
                add(master.packing_date, master.item_id, '포장', 'IN', box.box_qty, master.packing_no, box.package_lot_no)
        for master in db.query(ShipmentMaster).filter(ShipmentMaster.status == 'CONFIRMED', ShipmentMaster.shipment_date.between(first, last)):
            for item in master.items:
                # Direct dispatch consumes the source production item; it does not create finished stock.
                if item.direct_lots:
                    for lot in item.direct_lots:
                        add(master.shipment_date, lot.source_item_id, '직출고', 'OUT', lot.shipped_qty, master.shipment_no, lot.source_lot_no)
                else:
                    for box in item.boxes:
                        add(master.shipment_date, item.item_id, '출고', 'OUT', box.shipped_qty, master.shipment_no, box.package_lot_no)
                    if not item.boxes:
                        add(master.shipment_date, item.item_id, '출고', 'OUT', item.shipped_qty, master.shipment_no)
        for row in db.query(QualityProductionDefect).filter(QualityProductionDefect.status == 'ACTIVE', QualityProductionDefect.defect_date.between(first, last)):
            add(row.defect_date, row.item_id, '재고불량', 'OUT', row.defect_qty, f'불량 #{row.id}', row.lot_no)
        for row in db.query(InventoryAdjustmentModel).filter(InventoryAdjustmentModel.created_at >= datetime.fromisoformat(first), InventoryAdjustmentModel.created_at < datetime.fromisoformat(last) + timedelta(days=1)):
            add(row.created_at.date().isoformat(), row.item_id, '재고조정', 'IN' if row.adjustment_qty>0 else 'OUT', abs(row.adjustment_qty), f'조정 #{row.id}', row.lot_no, row.reason)
    else:
        warnings.append('외부 연동은 생산·외주입고검사·포장·출고 자료 기준입니다. 구매입고·외주출고·공정투입은 자동 연동 자료가 없어 수불 보완 입력이 필요합니다. 계산재고는 검토용입니다.')
        for row in db.query(ExternalProductionRecord).filter(ExternalProductionRecord.work_date.between(first, last)):
            raw = json.loads(row.raw_json)
            setup = raw.get('F10')
            if setup in (None, ''):
                warnings.append('외부 생산실적 중 SET-UP 미수신 건이 있습니다. 해당 생산·불량은 수신 수량으로 표시하며 SET-UP과 투입수량은 별도 확인이 필요합니다.')
            gross = number(row.job_qty)+number(row.fault_qty)+number(setup)
            external(row.work_date, row.part_no, row.process_name, '생산', 'IN', gross, row.job_no, row.lot_no)
            external(row.work_date, row.part_no, row.process_name, '생산불량', 'OUT', row.fault_qty, row.job_no, row.lot_no)
            external(row.work_date, row.part_no, row.process_name, 'SET-UP', 'OUT', setup, row.job_no, row.lot_no)
        for row in db.query(ExternalSubcontractFault).filter(ExternalSubcontractFault.present.is_(True), ExternalSubcontractFault.confirm_date.between(first, last)):
            raw = json.loads(row.raw_json)
            received = raw.get('ORI_LOT_QTY')
            if received in (None, ''):
                warnings.append(f'외주 {row.inbound_no}: 입고수량 미수신으로 입고 집계 제외.');continue
            note = '외부 입고검사 확정일 기준'
            external(row.confirm_date, row.part_no, row.process_name, '외주입고', 'IN', received, row.inbound_no, row.lot_no, note)
            for field, label in [('REST_QTY','외주불량'),('LOSS_QTY','LOSS'),('SAMPLE_QTY','샘플'),('KIPUP_QTY','킵업')]:
                external(row.confirm_date,row.part_no,row.process_name,label,'OUT',(raw.get(field) or row.defect_qty) if field=='REST_QTY' else raw.get(field),row.inbound_no,row.lot_no,note)
        missing = {r.record_id for r in db.query(PackingSourcePresence).filter(PackingSourcePresence.present.is_(False))}
        for row in db.query(ExternalPackingRecord).filter(ExternalPackingRecord.packing_date.between(first, last)):
            if row.id not in missing:
                external(row.packing_date,row.part_no,'포장','포장','IN',row.packing_qty,'포장',row.lot_no)
        for row, master in (db.query(ExternalShipmentLot, ExternalShipment)
                            .join(ExternalShipment, ExternalShipment.id == ExternalShipmentLot.shipment_id)
                            .filter(ExternalShipment.present.is_(True), ExternalShipment.status == 'OUT_FIN', ExternalShipment.shipment_date.between(first,last))):
            external(master.shipment_date,row.part_no,'출고','출고','OUT',row.allocated_qty,master.shipment_no,row.lot_no)
    for row in db.query(InventoryLedgerEntry).filter(InventoryLedgerEntry.source==source, InventoryLedgerEntry.entry_date.between(first,last), InventoryLedgerEntry.voided.is_(False)):
        add(row.entry_date,row.item_id,row.category,row.direction,row.qty,row.document_no,row.lot_no,'수불 보완: '+row.reason,entry_id=row.id)
    return sorted(result,key=lambda row:(row['date'],row['document_no'],row['lot_no'])), list(dict.fromkeys(warnings)), connections
