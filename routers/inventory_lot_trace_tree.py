from collections import deque

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.models import ProcessModel, PurchaseInboundItem, PurchaseInboundMaster
from models.packing import PackingBox, PackingMaster
from models.production import ProductionPerformance
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
        }

    return {"lot_no": lot_no, "process_name": "연결", "date": "", "qty": 0.0, "part_no": ""}


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


@router.get("/api/inventory/lot-trace/tree")
def inventory_lot_trace_tree(
    lot_no: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    root = lot_no.strip()
    all_edges = _edges(db)
    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for edge in all_edges:
        a = edge["parent_lot_no"]
        b = edge["child_lot_no"]
        adjacency.setdefault(a, []).append((b, edge))
        adjacency.setdefault(b, []).append((a, edge))

    depth = {root: 0}
    parent: dict[str, str | None] = {root: None}
    queue = deque([root])
    while queue and len(depth) < 500:
        current = queue.popleft()
        for neighbor, _ in adjacency.get(current, []):
            if neighbor in depth:
                continue
            depth[neighbor] = depth[current] + 1
            parent[neighbor] = current
            queue.append(neighbor)

    process_map = _process_map(db)
    node_cache: dict[str, dict] = {}

    def node(value: str) -> dict:
        if value not in node_cache:
            node_cache[value] = _node(db, value, process_map)
        return node_cache[value]

    def path_to(value: str) -> list[str]:
        path = []
        current: str | None = value
        guard = 0
        while current is not None and guard < 500:
            path.append(current)
            current = parent.get(current)
            guard += 1
        path.reverse()
        return path

    oriented: list[tuple[str, str, dict]] = []
    for edge in all_edges:
        a = edge["parent_lot_no"]
        b = edge["child_lot_no"]
        if a not in depth or b not in depth:
            continue
        if depth[a] < depth[b]:
            current, lower = a, b
        elif depth[b] < depth[a]:
            current, lower = b, a
        else:
            current, lower = (a, b) if a <= b else (b, a)
        oriented.append((current, lower, edge))

    oriented.sort(key=lambda x: (depth.get(x[0], 999), path_to(x[0]), x[1]))
    rows = []
    has_child: set[str] = set()
    for current, lower, edge in oriented:
        has_child.add(current)
        current_node = node(current)
        lower_node = node(lower)
        tree = path_to(current) + [lower]
        rows.append({
            "process": current_node["process_name"],
            "lot_no": current,
            "lot_date": current_node["date"],
            "part_no": current_node["part_no"],
            "child_lot_no": lower,
            "child_lot_qty": lower_node["qty"],
            "child_part_no": lower_node["part_no"],
            "consumed_qty": float(edge.get("qty") or 0),
            "tree": " - ".join(tree),
        })

    for value in sorted(depth, key=lambda x: (depth[x], path_to(x), x)):
        if value in has_child:
            continue
        current_node = node(value)
        rows.append({
            "process": current_node["process_name"],
            "lot_no": value,
            "lot_date": current_node["date"],
            "part_no": current_node["part_no"],
            "child_lot_no": "",
            "child_lot_qty": None,
            "child_part_no": "",
            "consumed_qty": None,
            "tree": " - ".join(path_to(value)),
        })

    rows.sort(key=lambda row: (row["tree"].count(" - "), row["tree"], row["lot_no"], row["child_lot_no"]))
    return {
        "root_lot_no": root,
        "root": node(root),
        "rows": rows,
        "row_count": len(rows),
        "lot_count": len(depth),
    }
