from collections import deque

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.models import PurchaseInboundItem, PurchaseInboundMaster
from models.packing import PackingBox, PackingMaster
from models.production_lot import ProductionLotModel
from models.sales import ShipmentBox, ShipmentDirectLot, ShipmentItem, ShipmentMaster

router = APIRouter(tags=["Inventory Lot Trace"])
templates = Jinja2Templates(directory="templates")


@router.get("/inventory/lot-trace", response_class=HTMLResponse)
def inventory_lot_trace_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="inventory_lot_trace.html",
        context={"request": request, "user": current_user},
    )


def _node_details(db: Session, lot_no: str) -> dict:
    purchase = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(PurchaseInboundItem.internal_lot_no == lot_no)
        .first()
    )
    if purchase:
        item, master = purchase
        return {
            "lot_no": lot_no,
            "type": "구매입고",
            "part_no": item.part_no,
            "qty": float(item.inbound_qty or 0),
            "date": master.inbound_date or "",
            "reference": master.inbound_no or "",
        }

    production = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot_no).first()
    if production:
        return {
            "lot_no": lot_no,
            "type": "생산/공정",
            "part_no": production.part_no,
            "qty": float(production.lot_qty or 0),
            "date": production.created_at.strftime("%Y-%m-%d") if production.created_at else "",
            "reference": production.note or "",
        }

    packing = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingBox.package_lot_no == lot_no)
        .first()
    )
    if packing:
        box, master = packing
        shipment_box = db.query(ShipmentBox).filter(ShipmentBox.packing_box_id == box.id).first()
        shipment_no = ""
        if shipment_box and shipment_box.shipment_item and shipment_box.shipment_item.shipment:
            shipment_no = shipment_box.shipment_item.shipment.shipment_no
        return {
            "lot_no": lot_no,
            "type": "포장/출고",
            "part_no": master.part_no,
            "qty": float(box.box_qty or 0),
            "date": master.packing_date or "",
            "reference": shipment_no or master.packing_no or "",
        }

    direct_rows = (
        db.query(ShipmentDirectLot, ShipmentItem, ShipmentMaster)
        .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
        .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
        .filter(ShipmentDirectLot.outbound_lot_no == lot_no)
        .all()
    )
    if direct_rows:
        qty = sum(float(row.shipped_qty or 0) for row, _, _ in direct_rows)
        direct, shipment_item, shipment = direct_rows[0]
        return {
            "lot_no": lot_no,
            "type": "포장/출고(샘플·개발)",
            "part_no": shipment_item.part_no,
            "qty": qty,
            "date": shipment.shipment_date or "",
            "reference": shipment.shipment_no or "",
        }

    return {
        "lot_no": lot_no,
        "type": "연결 LOT",
        "part_no": "",
        "qty": 0,
        "date": "",
        "reference": "",
    }


@router.get("/api/inventory/lot-trace")
def inventory_lot_trace(
    lot_no: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    root = lot_no.strip()
    relations = db.query(LotRelationModel).all()
    parent_map: dict[str, list[LotRelationModel]] = {}
    child_map: dict[str, list[LotRelationModel]] = {}
    for relation in relations:
        parent_map.setdefault(relation.parent_lot_no, []).append(relation)
        child_map.setdefault(relation.child_lot_no, []).append(relation)

    queue = deque([root])
    visited: set[str] = set()
    edges = []
    edge_keys = set()
    while queue and len(visited) < 200:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        for relation in parent_map.get(current, []):
            key = (relation.parent_lot_no, relation.child_lot_no, relation.process_code)
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({
                    "parent_lot_no": relation.parent_lot_no,
                    "child_lot_no": relation.child_lot_no,
                    "process_code": relation.process_code or "",
                    "qty": float(relation.consumed_qty or 0),
                })
            if relation.child_lot_no not in visited:
                queue.append(relation.child_lot_no)

        for relation in child_map.get(current, []):
            key = (relation.parent_lot_no, relation.child_lot_no, relation.process_code)
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({
                    "parent_lot_no": relation.parent_lot_no,
                    "child_lot_no": relation.child_lot_no,
                    "process_code": relation.process_code or "",
                    "qty": float(relation.consumed_qty or 0),
                })
            if relation.parent_lot_no not in visited:
                queue.append(relation.parent_lot_no)

    # 포장 LOT는 LotRelation에 직접 들어가지 않는 기존 양산 데이터가 있으므로 원 생산 LOT를 보강합니다.
    packing = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingBox.package_lot_no == root)
        .first()
    )
    if packing:
        _, master = packing
        for allocation in master.allocations:
            source = allocation.source_lot_no
            visited.add(source)
            key = (source, root, "PACKING")
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append({
                    "parent_lot_no": source,
                    "child_lot_no": root,
                    "process_code": "PACKING",
                    "qty": float(allocation.allocated_qty or 0),
                })

    nodes = [_node_details(db, value) for value in sorted(visited | {root})]
    nodes.sort(key=lambda row: (0 if row["lot_no"] == root else 1, row["date"], row["lot_no"]))
    return {
        "root_lot_no": root,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
