"""Read-only FIFO planning projection. Never reserves or consumes real inventory."""
from collections import defaultdict
from sqlalchemy.orm import selectinload
from models.models import ItemMasterModel, ItemBomModel, ProcessModel
from models.sales import SalesOrderMaster, SalesOrderItem, ShipmentMaster, ShipmentItem, ShipmentBox, ShipmentDirectLot
from models.production import ProductionPlan, ProductionWorkOrder, ProductionPerformance
from models.production_lot import ProductionLotModel
from models.subcontract_outbound import SubcontractOutboundMaster, SubcontractOutboundItem
from models.subcontract_inbound import SubcontractInboundMaster, SubcontractInboundItem, SubcontractInboundLot
from models.quality import QualityInboundResult
from services.production_sync_mapping import is_finished, stage_suffix
from services.production_lot_service import performance_id_from_lot_note


def fifo_allocate(orders, supplies):
    """Inputs are already ordered. A supply unit can cover only one open demand."""
    pool = [dict(s, remaining=float(s['qty'])) for s in supplies]
    result = []
    for order in orders:
        demand = float(order['remaining_qty'])
        allocations = []
        for supply in pool:
            if supply['item_id'] != order['item_id'] or supply['remaining'] <= 0:
                continue
            qty = min(demand, supply['remaining'])
            if qty <= 0:
                break
            allocations.append({k: v for k, v in supply.items() if k not in {'remaining', 'qty'}} | {'qty': qty})
            supply['remaining'] -= qty
            demand -= qty
        stock = sum(a['qty'] for a in allocations if a['kind'] == 'STOCK')
        packed = sum(a['qty'] for a in allocations if a['kind'] == 'PACKED')
        planned = sum(a['qty'] for a in allocations if a['kind'] == 'WORK_ORDER')
        result.append(order | {'allocations': allocations, 'stock_allocated_qty': stock,
            'packed_allocated_qty': packed, 'production_mapped_qty': planned,
            'new_production_required_qty': max(order['remaining_qty'] - stock - packed, 0),
            'unmapped_qty': max(demand, 0),
            'shipping_ready_rate': 100 * (stock + packed) / order['remaining_qty'] if order['remaining_qty'] else 0})
    return result


def pipeline_data(db, part_no='', user=None):
    # Reuse the exact balance projection used by LOT stock: cancellations,
    # consumption, adjustments and external packing conflicts are handled there.
    from routers.inventory_lot_location import inventory_lots_with_current_location
    masters = db.query(ItemMasterModel).all()
    by_id = {i.id: i for i in masters}
    by_part = {i.part_no: i for i in masters}
    edges = defaultdict(set)
    for bom in db.query(ItemBomModel):
        parent = by_id.get(bom.parent_item_id) or by_part.get(bom.parent_part_no)
        child = by_id.get(bom.child_item_id) or by_part.get(bom.child_part_no)
        if parent and child:
            edges[parent.id].add(child.id)
    # Allocate globally before applying the screen filter so filtering cannot
    # reassign stock that belongs to earlier orders.
    demand_rows = db.query(SalesOrderItem, SalesOrderMaster).join(SalesOrderMaster).filter(
        SalesOrderMaster.status.in_(['ORDERED', 'PARTIAL']),
        SalesOrderItem.order_qty > SalesOrderItem.shipped_qty).order_by(
        SalesOrderMaster.order_date, SalesOrderMaster.id, SalesOrderItem.id).all()
    roots = {line.item_id for line, _ in demand_rows}
    related = {}
    for root in roots:
        seen, todo = {root}, [root]
        while todo:
            for child in edges[todo.pop()]:
                if child not in seen:
                    seen.add(child)
                    todo.append(child)
        related[root] = seen
    all_ids = set().union(*related.values()) if related else set()
    parts = [by_id[i].part_no for i in all_ids if i in by_id]
    lots = inventory_lots_with_current_location(part_no=parts, stock_status='REMAINING', db=db, current_user=user)['items'] if parts else []
    supplies = []
    for row in sorted(lots, key=lambda r: (str(r['created_at']), r['lot_no'])):
        item = by_id.get(row['item_id'])
        if item and is_finished(item):
            supplies.append({'item_id': item.id, 'kind': 'PACKED' if row['source'] in {'MES 포장', '외부 포장'} else 'STOCK',
                             'lot_no': row['lot_no'], 'qty': row['remaining_qty'], 'work_order_no': ''})
    performance_orders = {p.id: p.work_order_id for p in db.query(ProductionPerformance)}
    output_lots = defaultdict(list)
    for lot in db.query(ProductionLotModel).filter(ProductionLotModel.status == 'ACTIVE'):
        order_id = performance_orders.get(performance_id_from_lot_note(lot.note))
        if order_id:
            output_lots[order_id].append(lot.lot_no)
    work_orders = db.query(ProductionWorkOrder).filter(ProductionWorkOrder.status.in_(['WAITING', 'IN_PROGRESS'])).order_by(
        ProductionWorkOrder.order_date, ProductionWorkOrder.id).all()
    for order in work_orders:
        remaining = max(float(order.order_qty) - float(order.production_qty), 0)
        if remaining:
            supplies.append({'item_id': order.item_id, 'kind': 'WORK_ORDER', 'qty': remaining,
                             'work_order_no': order.work_order_no, 'lot_no': '', 'output_lots': output_lots[order.id]})
    orders = [{'id': line.id, 'item_id': line.item_id, 'part_no': by_id[line.item_id].part_no if line.item_id in by_id else line.part_no,
               'order_no': master.order_no, 'order_date': master.order_date, 'due_date': line.delivery_date or master.delivery_due_date,
               'order_qty': float(line.order_qty), 'shipped_qty': float(line.shipped_qty),
               'remaining_qty': max(float(line.order_qty) - float(line.shipped_qty), 0)} for line, master in demand_rows]
    allocated = fifo_allocate(orders, supplies)
    # Actual confirmed shipment links are separate from provisional FIFO mapping.
    actual = defaultdict(list)
    for shipment_item, shipment in db.query(ShipmentItem, ShipmentMaster).join(ShipmentMaster).options(
            selectinload(ShipmentItem.boxes), selectinload(ShipmentItem.direct_lots)).filter(ShipmentMaster.status == 'CONFIRMED'):
        for box in shipment_item.boxes:
            actual[shipment_item.sales_order_item_id].append({'lot_no': box.package_lot_no, 'qty': box.shipped_qty, 'shipment_no': shipment.shipment_no})
        for lot in shipment_item.direct_lots:
            actual[shipment_item.sales_order_item_id].append({'lot_no': lot.source_lot_no, 'qty': lot.shipped_qty, 'shipment_no': shipment.shipment_no})
    for order in allocated:
        order['confirmed_shipments'] = actual[order['id']]
    plan_qty = defaultdict(float)
    for plan in db.query(ProductionPlan):
        plan_qty[plan.item_id] += float(plan.plan_qty)
    active_qty = defaultdict(float)
    for order in work_orders:
        active_qty[order.item_id] += max(float(order.order_qty) - float(order.production_qty), 0)
    returned = defaultdict(float)
    for item in db.query(SubcontractInboundItem).join(SubcontractInboundMaster).filter(SubcontractInboundMaster.status == 'RECEIVED'):
        returned[item.outbound_item_id] += float(item.good_qty)
    plating = defaultdict(float)
    for item, master in db.query(SubcontractOutboundItem, SubcontractOutboundMaster).join(SubcontractOutboundMaster).filter(
            SubcontractOutboundMaster.status == 'OUTBOUND'):
        if '도금' in (master.processing_type_name or ''):
            plating[item.item_id] += max(float(item.outbound_qty) - returned[item.id], 0)
    waiting_inspection = defaultdict(float)
    available_lots = {(row['item_id'], row['lot_no']): float(row['remaining_qty']) for row in lots}
    quality = {q.inbound_item_id: q for q in db.query(QualityInboundResult).filter(QualityInboundResult.source_type == 'SUBCONTRACT')}
    for item in db.query(SubcontractInboundItem).options(selectinload(SubcontractInboundItem.lots)).join(SubcontractInboundMaster).filter(SubcontractInboundMaster.status == 'RECEIVED'):
        q = quality.get(item.id)
        if not q or q.inspection_status != 'COMPLETED':
            for lot in item.lots:
                key = (item.item_id, lot.child_lot_no)
                qty = min(float(lot.good_qty), available_lots.get(key, 0))
                waiting_inspection[item.item_id] += qty
                available_lots[key] = max(available_lots.get(key, 0) - qty, 0)
    processes = {p.process_code: p.process_name for p in db.query(ProcessModel)}
    stock_by_stage = defaultdict(lambda: defaultdict(float))
    for lot in lots:
        item = by_id.get(lot['item_id'])
        if item and not is_finished(item):
            suffix = stage_suffix(processes.get(item.production_loc, ''), item.part_no)
            if suffix is None:
                suffix = next((s for s in ['-Ag', '-A', '-B', '-C', '-D'] if item.part_no.lower().endswith(s.lower())), None)
            key = 'assembly_qty' if suffix == '-C' else ('machining_qty' if suffix in {'-A', '-B', '-D'} else None)
            if key:
                stock_by_stage[item.id][key] += float(lot['remaining_qty'])
    rows = []
    for root in sorted(roots, key=lambda i: by_id[i].part_no if i in by_id else ''):
        item = by_id.get(root)
        if not item:
            continue
        demands = [o for o in allocated if o['item_id'] == root]
        remaining = sum(o['remaining_qty'] for o in demands)
        stock = sum(o['stock_allocated_qty'] for o in demands)
        packed = sum(o['packed_allocated_qty'] for o in demands)
        ids = related[root]
        shared = any(ids.intersection(related[other] - {other}) for other in roots if other != root)
        rows.append({'item_id': root, 'part_no': item.part_no, 'part_name': item.part_name,
            'sales_order_qty': remaining, 'finished_stock_qty': sum(s['qty'] for s in supplies if s['item_id'] == root and s['kind'] == 'STOCK'),
            'stock_allocated_qty': stock, 'packed_ready_qty': sum(s['qty'] for s in supplies if s['item_id'] == root and s['kind'] == 'PACKED'),
            'packed_allocated_qty': packed, 'new_production_required_qty': remaining - stock - packed,
            'shipping_ready_rate': 100 * (stock + packed) / remaining if remaining else 0,
            'plan_qty': sum(plan_qty[i] for i in ids), 'work_order_qty': sum(active_qty[i] for i in ids),
            'machining_qty': sum(stock_by_stage[i]['machining_qty'] for i in ids),
            'subcontract_plating_qty': sum(plating[i] for i in ids), 'inspection_qty': sum(waiting_inspection[i] for i in ids),
            'assembly_qty': sum(stock_by_stage[i]['assembly_qty'] for i in ids), 'vision_inspection_qty': None,
            'shared_bom': shared})
    keyword = part_no.strip().casefold()
    selected_ids = {r['item_id'] for r in rows if not keyword or keyword in r['part_no'].casefold()}
    return {'items': [r for r in rows if r['item_id'] in selected_ids],
            'orders': [o for o in allocated if o['item_id'] in selected_ids],
            'allocation_mode': 'PROJECTION',
            'notes': ['FIFO 예정 배정은 조회 기준이며 실제 재고를 예약·차감하지 않습니다.',
                      '가공·조립은 현재 LOT 잔량, 외주도금은 출고 후 미입고 수량, 검사는 외주입고 검사 대기 수량입니다.',
                      '계획은 등록된 누적 계획량, 지시는 미완료 지시 잔량입니다. 단계별 수량을 더하면 중복됩니다.',
                      'BOM으로 연결된 품목만 포함하며 공용 부품 수량은 여러 완제품에 중복 표시될 수 있습니다.',
                      '외부 가공·조립 실적은 재고 원장이 아니므로 재공 재고로 간주하지 않습니다. 외부 포장은 기존 재고 연결 정책을 따릅니다.',
                      '비전은 별도 실적 원장이 없어 미집계합니다. 수주 배정은 동일 품목의 완제품 재고와 작업지시에 한합니다.']}
