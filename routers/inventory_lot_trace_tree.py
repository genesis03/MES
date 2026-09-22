from collections import deque

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_consumption import LotConsumptionModel
from models.lot_relation import LotRelationModel
from models.models import ProcessModel, PurchaseInboundItem, PurchaseInboundMaster
from models.packing import PackingBox, PackingMaster
from models.production import ProductionPerformance, ProductionWorkOrder
from models.production_lot import ProductionLotModel
from models.sales import ShipmentBox, ShipmentDirectLot, ShipmentItem, ShipmentMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from services.production_lot_service import performance_id_from_lot_note

router = APIRouter(tags=["Inventory Lot Trace Tree"])


def _process_map(db: Session) -> dict[str, str]:
    return {
        row.process_code: (row.process_name or row.process_code)
        for row in db.query(ProcessModel).all()
        if row.process_code
    }


def _node(db: Session, lot_no: str, process_map: dict[str, str]) -> dict:
    direct = (
        db.query(ShipmentDirectLot, ShipmentItem, ShipmentMaster)
        .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
        .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
        .filter(ShipmentDirectLot.outbound_lot_no == lot_no, ShipmentMaster.status == "CONFIRMED")
        .order_by(ShipmentMaster.id.desc(), ShipmentDirectLot.id.desc())
        .first()
    )
    if direct:
        row, item, shipment = direct
        return {
            "lot_no": lot_no,
            "process_name": "출고",
            "date": shipment.shipment_date or "",
            "qty": float(row.shipped_qty or 0),
            "part_no": item.part_no or "",
        }

    packing = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingBox.package_lot_no == lot_no, PackingMaster.status == "PACKED")
        .order_by(PackingMaster.id.desc(), PackingBox.id.desc())
        .first()
    )
    if packing:
        box, master = packing
        shipped = (
            db.query(ShipmentBox, ShipmentItem, ShipmentMaster)
            .join(ShipmentItem, ShipmentItem.id == ShipmentBox.shipment_item_id)
            .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
            .filter(ShipmentBox.package_lot_no == lot_no, ShipmentMaster.status == "CONFIRMED")
            .order_by(ShipmentMaster.id.desc())
            .first()
        )
        return {
            "lot_no": lot_no,
            "process_name": "출고" if shipped else "포장",
            "date": (shipped[2].shipment_date if shipped else master.packing_date) or "",
            "qty": float(box.box_qty or 0),
            "part_no": master.part_no or "",
        }

    purchase = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(PurchaseInboundItem.internal_lot_no == lot_no, PurchaseInboundMaster.status == "CONFIRMED")
        .first()
    )
    if purchase:
        item, master = purchase
        return {
            "lot_no": lot_no,
            "process_name": "구매입고",
            "date": master.inbound_date or "",
            "qty": float(item.inbound_qty or 0),
            "part_no": item.part_no or "",
            "external_lot_no": item.supplier_lot_no or "",
        }

    inbound = (
        db.query(SubcontractInboundLot, SubcontractInboundItem, SubcontractInboundMaster)
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(SubcontractInboundMaster.status == "RECEIVED", SubcontractInboundLot.child_lot_no == lot_no)
        .order_by(SubcontractInboundMaster.id.desc())
        .first()
    )
    if inbound:
        lot, item, master = inbound
        return {
            "lot_no": lot_no,
            "process_name": master.processing_type_name or "외주가공",
            "date": master.inbound_date or "",
            "qty": float(lot.good_qty or 0),
            "part_no": item.part_no or "",
            "external_lot_no": lot.supplier_lot_no or "",
        }

    production = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot_no).first()
    if production:
        process_name = "공정"
        perf_id = performance_id_from_lot_note(production.note)
        if perf_id is not None:
            perf = db.get(ProductionPerformance, perf_id)
            if perf is not None:
                if (perf.performance_type or "").upper() == "ASSEMBLY":
                    process_name = "조립"
                else:
                    process_name = process_map.get(perf.process_code, perf.process_code or "가공")
        return {
            "lot_no": lot_no,
            "process_name": process_name,
            "date": production.created_at.strftime("%Y-%m-%d") if production.created_at else "",
            "qty": float(production.lot_qty or 0),
            "part_no": production.part_no or "",
            "external_lot_no": "",
        }

    return {"lot_no": lot_no, "process_name": "연결", "date": "", "qty": 0.0, "part_no": "", "external_lot_no": ""}


def _edges(db: Session) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add(parent: str, child: str, process_code: str, qty: float):
        parent = str(parent or "").strip()
        child = str(child or "").strip()
        if not parent or not child or parent == child:
            return
        key = (parent, child, process_code or "")
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "parent_lot_no": parent,
            "child_lot_no": child,
            "process_code": process_code or "",
            "qty": float(qty or 0),
        })

    for relation in db.query(LotRelationModel).all():
        add(relation.parent_lot_no, relation.child_lot_no, relation.process_code or "", relation.consumed_qty or 0)

    # 생산실적의 실제 자재 LOT 소비원장을 생산 LOT 계보에 연결합니다.
    # 다중 BOM/다중 LOT 배정도 performance_id 기준으로 모두 연결합니다.
    consumptions = db.query(LotConsumptionModel).all()
    perf_ids = sorted({row.performance_id for row in consumptions if row.performance_id})
    output_by_perf: dict[int, list[ProductionLotModel]] = {}
    if perf_ids:
        output_lots = db.query(ProductionLotModel).filter(ProductionLotModel.note.like("PERF:%")).all()
        for output_lot in output_lots:
            perf_id = performance_id_from_lot_note(output_lot.note)
            if perf_id in perf_ids:
                output_by_perf.setdefault(perf_id, []).append(output_lot)
    for consumption in consumptions:
        for output_lot in output_by_perf.get(consumption.performance_id, []):
            add(
                consumption.lot_no,
                output_lot.lot_no,
                consumption.process_code or "",
                consumption.consumed_qty or 0,
            )

    for box, master in (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingMaster.status == "PACKED")
        .all()
    ):
        for allocation in master.allocations:
            add(
                allocation.source_lot_no,
                box.package_lot_no,
                "PACKING",
                min(float(allocation.allocated_qty or 0), float(box.box_qty or 0)),
            )

    for direct, _, _ in (
        db.query(ShipmentDirectLot, ShipmentItem, ShipmentMaster)
        .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
        .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
        .filter(ShipmentMaster.status == "CONFIRMED")
        .all()
    ):
        if direct.outbound_lot_no:
            add(direct.source_lot_no, direct.outbound_lot_no, "SHIPMENT", direct.shipped_qty or 0)

    return rows


def _production_performance_for_lot_state(
    db: Session,
    lot_no: str,
    part_no: str,
    expected_qty: float | None = None,
):
    """생산 LOT의 원 생산실적을 찾습니다.

    정상 LOT는 PERF note로 정확히 연결하고, 외주 전량입고 과정에서 note/품번이
    덮어써진 기존 LOT는 LOT 최초 생성시각 + 당시 품번 + 생산수량으로 복구합니다.
    """
    stock = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot_no).first()
    if stock is None:
        return None

    perf_id = performance_id_from_lot_note(stock.note)
    if perf_id is not None:
        perf = db.get(ProductionPerformance, perf_id)
        if perf is not None:
            order = db.get(ProductionWorkOrder, perf.work_order_id)
            if not part_no or (order and order.part_no == part_no):
                return perf

    query = (
        db.query(ProductionPerformance)
        .join(ProductionWorkOrder, ProductionWorkOrder.id == ProductionPerformance.work_order_id)
        .filter(ProductionWorkOrder.part_no == part_no)
    )
    if stock.created_at:
        created_date = stock.created_at.strftime("%Y-%m-%d")
        same_day = query.filter(ProductionPerformance.performance_date == created_date).all()
    else:
        same_day = []

    candidates = same_day or query.order_by(ProductionPerformance.created_at.desc()).limit(50).all()
    if not candidates:
        return None

    target_qty = float(expected_qty if expected_qty is not None else (stock.lot_qty or 0))
    target_time = stock.created_at

    def rank(perf: ProductionPerformance):
        qty_gap = abs(float(perf.good_qty or 0) - target_qty)
        if target_time and perf.created_at:
            time_gap = abs((target_time - perf.created_at).total_seconds())
        else:
            time_gap = float("inf")
        return (qty_gap, time_gap, perf.id)

    return min(candidates, key=rank)


def _same_lot_inbound_for_state(db: Session, lot_no: str, part_no: str):
    return (
        db.query(SubcontractInboundLot, SubcontractInboundItem, SubcontractInboundMaster)
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.child_lot_no == lot_no,
            SubcontractInboundLot.source_lot_no == lot_no,
            SubcontractInboundItem.part_no == part_no,
        )
        .order_by(SubcontractInboundMaster.id.desc(), SubcontractInboundLot.id.desc())
        .first()
    )


@router.get("/api/inventory/lot-trace/tree")
def inventory_lot_trace_tree(
    lot_no: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    root = lot_no.strip()
    all_edges = _edges(db)

    # 생산/외주 외의 포장·출고·분할외주 관계용 역방향 인덱스입니다.
    parent_edges: dict[str, list[dict]] = {}
    for edge in all_edges:
        parent_edges.setdefault(edge["child_lot_no"], []).append(edge)

    process_map = _process_map(db)
    node_cache: dict[str, dict] = {}

    def node(value: str) -> dict:
        if value not in node_cache:
            node_cache[value] = _node(db, value, process_map)
            node_cache[value].setdefault("external_lot_no", "")
        return node_cache[value]

    root_node = node(root)
    rows: list[dict] = []
    visited_states: set[tuple[str, str]] = set()

    def state_label(lot_value: str, part_value: str) -> str:
        return f"{lot_value} ({part_value})" if part_value else lot_value

    def walk(
        current_lot: str,
        current_part: str,
        path: list[str],
        expected_qty: float | None = None,
        depth: int = 0,
    ):
        if depth >= 100 or len(rows) >= 500:
            return

        state_key = (current_lot, current_part or "")
        if state_key in visited_states:
            return
        visited_states.add(state_key)

        current_node = node(current_lot)

        # 1) 외주 전량입고로 LOT 번호가 유지된 경우:
        #    동일 LOT라도 가공 후 품번 -> 가공 전 품번을 하나의 공정변환으로 표시합니다.
        same_lot = _same_lot_inbound_for_state(db, current_lot, current_part)
        if same_lot:
            inbound_lot, inbound_item, inbound_master = same_lot
            previous_part = inbound_item.previous_part_no or ""
            next_path = path + [state_label(current_lot, previous_part)]
            rows.append({
                "process": inbound_master.processing_type_name or current_node["process_name"],
                "lot_no": current_lot,
                "lot_date": inbound_master.inbound_date or current_node["date"],
                "part_no": current_part or inbound_item.part_no or current_node["part_no"],
                "external_lot_no": inbound_lot.supplier_lot_no or "",
                "child_lot_no": current_lot,
                "child_lot_qty": float(inbound_lot.source_qty or 0),
                "child_part_no": previous_part,
                "consumed_qty": float(inbound_lot.good_qty or 0),
                "tree": " - ".join(next_path),
            })
            walk(
                current_lot,
                previous_part,
                next_path,
                expected_qty=float(inbound_lot.source_qty or 0),
                depth=depth + 1,
            )
            return

        # 2) 생산/조립 LOT는 실제 생산실적의 LotConsumption을 기준으로
        #    BOM에 투입된 모든 자재/반제품 LOT를 분기해서 역추적합니다.
        perf = _production_performance_for_lot_state(
            db,
            current_lot,
            current_part,
            expected_qty=expected_qty,
        )
        if perf is not None:
            consumptions = (
                db.query(LotConsumptionModel)
                .filter(LotConsumptionModel.performance_id == perf.id)
                .order_by(LotConsumptionModel.id.asc())
                .all()
            )
            if consumptions:
                process_name = (
                    "조립"
                    if (perf.performance_type or "").upper() == "ASSEMBLY"
                    else process_map.get(perf.process_code, perf.process_code or current_node["process_name"])
                )
                for consumption in consumptions:
                    source_lot = consumption.lot_no
                    source_node = node(source_lot)
                    source_part = consumption.part_no or source_node["part_no"]
                    next_path = path + [state_label(source_lot, source_part)]
                    rows.append({
                        "process": process_name,
                        "lot_no": current_lot,
                        "lot_date": perf.performance_date or current_node["date"],
                        "part_no": current_part or current_node["part_no"],
                        "external_lot_no": current_node.get("external_lot_no", ""),
                        "child_lot_no": source_lot,
                        "child_lot_qty": source_node["qty"],
                        "child_part_no": source_part,
                        "consumed_qty": float(consumption.consumed_qty or 0),
                        "tree": " - ".join(next_path),
                    })
                    walk(
                        source_lot,
                        source_part,
                        next_path,
                        expected_qty=float(consumption.consumed_qty or 0),
                        depth=depth + 1,
                    )
                return

        # 3) 포장/출고/분할 외주처럼 별도 LotRelation으로 연결된 단계입니다.
        incoming = parent_edges.get(current_lot, [])
        if incoming:
            for edge in incoming:
                source_lot = edge["parent_lot_no"]
                source_node = node(source_lot)
                source_part = source_node["part_no"]
                next_path = path + [state_label(source_lot, source_part)]
                rows.append({
                    "process": current_node["process_name"],
                    "lot_no": current_lot,
                    "lot_date": current_node["date"],
                    "part_no": current_part or current_node["part_no"],
                    "external_lot_no": current_node.get("external_lot_no", ""),
                    "child_lot_no": source_lot,
                    "child_lot_qty": source_node["qty"],
                    "child_part_no": source_part,
                    "consumed_qty": float(edge.get("qty") or 0),
                    "tree": " - ".join(next_path),
                })
                walk(
                    source_lot,
                    source_part,
                    next_path,
                    expected_qty=float(edge.get("qty") or 0),
                    depth=depth + 1,
                )
            return

        # 원소재/구매입고처럼 더 이상 이전 LOT가 없는 경우는 기준 LOT 단독 조회일 때만 표시합니다.
        if depth == 0 and not rows:
            rows.append({
                "process": current_node["process_name"],
                "lot_no": current_lot,
                "lot_date": current_node["date"],
                "part_no": current_part or current_node["part_no"],
                "external_lot_no": current_node.get("external_lot_no", ""),
                "child_lot_no": "",
                "child_lot_qty": None,
                "child_part_no": "",
                "consumed_qty": None,
                "tree": " - ".join(path),
            })

    walk(root, root_node["part_no"], [state_label(root, root_node["part_no"])])

    traced_lots = {root}
    for row in rows:
        if row["child_lot_no"]:
            traced_lots.add(row["child_lot_no"])

    return {
        "root_lot_no": root,
        "root": root_node,
        "rows": rows,
        "row_count": len(rows),
        "lot_count": len(traced_lots),
    }
