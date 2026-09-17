from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.lot_relation import LotRelationModel
from models.models import ProcessModel, PurchaseInboundItem, PurchaseInboundMaster
from models.packing import PackingBox, PackingMaster
from models.production_lot import ProductionLotModel
from models.sales import ShipmentBox, ShipmentDirectLot, ShipmentItem, ShipmentMaster
from models.subcontract_inbound import SubcontractInboundItem, SubcontractInboundLot, SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundItem, SubcontractOutboundLot, SubcontractOutboundMaster

router = APIRouter(tags=["Inventory Lot Trace"])
templates = Jinja2Templates(directory="templates")


@router.get("/inventory/lot-trace", response_class=HTMLResponse)
def inventory_lot_trace_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="inventory_lot_trace.html",
        context={"request": request, "user": current_user},
    )


def _process_name_map(db: Session) -> dict[str, str]:
    return {
        row.process_code: (row.process_name or row.process_code)
        for row in db.query(ProcessModel).all()
        if row.process_code
    }


def _shipment_for_package_lot(db: Session, lot_no: str):
    return (
        db.query(ShipmentBox, ShipmentItem, ShipmentMaster)
        .join(ShipmentItem, ShipmentItem.id == ShipmentBox.shipment_item_id)
        .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
        .filter(ShipmentBox.package_lot_no == lot_no)
        .order_by(ShipmentMaster.id.desc())
        .first()
    )


def _node_details(db: Session, lot_no: str) -> dict:
    direct_rows = (
        db.query(ShipmentDirectLot, ShipmentItem, ShipmentMaster)
        .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
        .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
        .filter(ShipmentDirectLot.outbound_lot_no == lot_no)
        .order_by(ShipmentMaster.id.desc(), ShipmentDirectLot.id.desc())
        .all()
    )
    if direct_rows:
        qty = sum(float(row.shipped_qty or 0) for row, _, _ in direct_rows)
        _, shipment_item, shipment = direct_rows[0]
        return {
            "lot_no": lot_no,
            "type": "출고 LOT",
            "part_no": shipment_item.part_no,
            "qty": qty,
            "date": shipment.shipment_date or "",
            "reference": shipment.shipment_no or "",
        }

    packing = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingBox.package_lot_no == lot_no)
        .order_by(PackingMaster.id.desc(), PackingBox.id.desc())
        .first()
    )
    if packing:
        box, master = packing
        shipment_row = _shipment_for_package_lot(db, lot_no)
        shipment_no = shipment_row[2].shipment_no if shipment_row else ""
        shipment_date = shipment_row[2].shipment_date if shipment_row else ""
        return {
            "lot_no": lot_no,
            "type": "출고 LOT" if shipment_row else "포장 LOT",
            "part_no": master.part_no,
            "qty": float(box.box_qty or 0),
            "date": shipment_date or master.packing_date or "",
            "reference": shipment_no or master.packing_no or "",
        }

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
            "type": "구매입고 LOT",
            "part_no": item.part_no,
            "qty": float(item.inbound_qty or 0),
            "date": master.inbound_date or "",
            "reference": master.inbound_no or "",
        }

    inbound = (
        db.query(SubcontractInboundLot, SubcontractInboundItem, SubcontractInboundMaster)
        .join(SubcontractInboundItem, SubcontractInboundItem.id == SubcontractInboundLot.inbound_item_id)
        .join(SubcontractInboundMaster, SubcontractInboundMaster.id == SubcontractInboundItem.inbound_id)
        .filter(
            SubcontractInboundMaster.status == "RECEIVED",
            SubcontractInboundLot.child_lot_no == lot_no,
        )
        .order_by(SubcontractInboundMaster.id.desc())
        .first()
    )
    if inbound:
        lot, item, master = inbound
        return {
            "lot_no": lot_no,
            "type": f"외주입고 LOT ({master.processing_type_name})",
            "part_no": item.part_no,
            "qty": float(lot.good_qty or 0),
            "date": master.inbound_date or "",
            "reference": master.inbound_no or "",
        }

    production = db.query(ProductionLotModel).filter(ProductionLotModel.lot_no == lot_no).first()
    if production:
        outbound = (
            db.query(SubcontractOutboundLot, SubcontractOutboundItem, SubcontractOutboundMaster)
            .join(SubcontractOutboundItem, SubcontractOutboundItem.id == SubcontractOutboundLot.outbound_item_id)
            .join(SubcontractOutboundMaster, SubcontractOutboundMaster.id == SubcontractOutboundItem.outbound_id)
            .filter(
                SubcontractOutboundMaster.status == "OUTBOUND",
                SubcontractOutboundLot.lot_no == lot_no,
            )
            .order_by(SubcontractOutboundMaster.id.desc())
            .first()
        )
        if outbound:
            _, _, master = outbound
            type_name = f"공정 LOT / 외주출고 ({master.processing_type_name})"
            reference = master.outbound_no or production.note or ""
        else:
            type_name = "공정 LOT"
            reference = production.note or ""
        return {
            "lot_no": lot_no,
            "type": type_name,
            "part_no": production.part_no,
            "qty": float(production.lot_qty or 0),
            "date": production.created_at.strftime("%Y-%m-%d") if production.created_at else "",
            "reference": reference,
        }

    return {
        "lot_no": lot_no,
        "type": "연결 LOT",
        "part_no": "",
        "qty": 0,
        "date": "",
        "reference": "",
    }


def _add_edge(edges: list[dict], edge_keys: set[tuple], parent: str, child: str, process_code: str, qty: float) -> None:
    parent = str(parent or "").strip()
    child = str(child or "").strip()
    if not parent or not child or parent == child:
        return
    key = (parent, child, process_code or "")
    if key in edge_keys:
        return
    edge_keys.add(key)
    edges.append({
        "parent_lot_no": parent,
        "child_lot_no": child,
        "process_code": process_code or "",
        "qty": float(qty or 0),
    })


def _all_trace_edges(db: Session) -> list[dict]:
    edges: list[dict] = []
    edge_keys: set[tuple] = set()

    for relation in db.query(LotRelationModel).all():
        _add_edge(
            edges,
            edge_keys,
            relation.parent_lot_no,
            relation.child_lot_no,
            relation.process_code or "",
            float(relation.consumed_qty or 0),
        )

    # 양산 포장 LOT(=출고 LOT)는 과거 데이터 중 LotRelation이 없는 건까지 보강합니다.
    packed_rows = (
        db.query(PackingBox, PackingMaster)
        .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
        .filter(PackingMaster.status == "PACKED")
        .all()
    )
    for box, master in packed_rows:
        for allocation in master.allocations:
            # 포장 마스터 전체 배정수량을 박스별로 정확히 나눌 정보가 없는 기존 구조라
            # 연결 표시는 박스 수량을 상한으로 하되 계보 연결 자체를 우선합니다.
            qty = min(float(allocation.allocated_qty or 0), float(box.box_qty or 0))
            _add_edge(edges, edge_keys, allocation.source_lot_no, box.package_lot_no, "PACKING", qty)

    # 샘플/개발 직출고는 생산 LOT -> 출고 LOT 관계를 직접 보강합니다.
    direct_rows = (
        db.query(ShipmentDirectLot, ShipmentItem, ShipmentMaster)
        .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
        .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
        .filter(ShipmentMaster.status == "CONFIRMED")
        .all()
    )
    for direct, _, _ in direct_rows:
        if direct.outbound_lot_no:
            _add_edge(
                edges,
                edge_keys,
                direct.source_lot_no,
                direct.outbound_lot_no,
                "SHIPMENT",
                float(direct.shipped_qty or 0),
            )

    return edges


@router.get("/api/inventory/lot-trace/search")
def inventory_lot_trace_search(
    search_type: str = Query(..., pattern="^(PROCESS|OUTBOUND)$"),
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = q.strip()
    if not keyword:
        raise HTTPException(422, "LOT 검색어를 입력하세요.")

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def append_result(kind: str, lot_no: str, part_no: str, qty: float, date: str, reference: str = ""):
        lot_no = str(lot_no or "").strip()
        if not lot_no or (kind, lot_no) in seen:
            return
        seen.add((kind, lot_no))
        results.append({
            "search_type": kind,
            "lot_no": lot_no,
            "part_no": part_no or "",
            "qty": float(qty or 0),
            "date": date or "",
            "reference": reference or "",
        })

    if search_type == "PROCESS":
        production_rows = (
            db.query(ProductionLotModel)
            .filter(ProductionLotModel.lot_no.contains(keyword, autoescape=True))
            .order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc())
            .limit(limit)
            .all()
        )
        for lot in production_rows:
            append_result(
                "PROCESS",
                lot.lot_no,
                lot.part_no,
                float(lot.lot_qty or 0),
                lot.created_at.strftime("%Y-%m-%d") if lot.created_at else "",
                lot.note or "",
            )

        remaining = max(limit - len(results), 0)
        if remaining:
            purchase_rows = (
                db.query(PurchaseInboundItem, PurchaseInboundMaster)
                .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
                .filter(
                    PurchaseInboundMaster.status == "CONFIRMED",
                    PurchaseInboundItem.internal_lot_no.contains(keyword, autoescape=True),
                )
                .order_by(PurchaseInboundMaster.created_at.desc(), PurchaseInboundItem.id.desc())
                .limit(remaining)
                .all()
            )
            for item, master in purchase_rows:
                append_result(
                    "PROCESS",
                    item.internal_lot_no,
                    item.part_no,
                    float(item.inbound_qty or 0),
                    master.inbound_date or "",
                    master.inbound_no or "",
                )
    else:
        packing_rows = (
            db.query(PackingBox, PackingMaster)
            .join(PackingMaster, PackingMaster.id == PackingBox.packing_id)
            .filter(
                PackingMaster.status == "PACKED",
                PackingBox.package_lot_no.contains(keyword, autoescape=True),
            )
            .order_by(PackingMaster.packing_date.desc(), PackingBox.id.desc())
            .limit(limit)
            .all()
        )
        for box, master in packing_rows:
            shipment_row = _shipment_for_package_lot(db, box.package_lot_no)
            shipment = shipment_row[2] if shipment_row else None
            append_result(
                "OUTBOUND",
                box.package_lot_no,
                master.part_no,
                float(box.box_qty or 0),
                (shipment.shipment_date if shipment else master.packing_date) or "",
                (shipment.shipment_no if shipment else master.packing_no) or "",
            )

        remaining = max(limit - len(results), 0)
        if remaining:
            direct_rows = (
                db.query(ShipmentDirectLot, ShipmentItem, ShipmentMaster)
                .join(ShipmentItem, ShipmentItem.id == ShipmentDirectLot.shipment_item_id)
                .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
                .filter(
                    ShipmentMaster.status == "CONFIRMED",
                    ShipmentDirectLot.outbound_lot_no.isnot(None),
                    ShipmentDirectLot.outbound_lot_no.contains(keyword, autoescape=True),
                )
                .order_by(ShipmentMaster.shipment_date.desc(), ShipmentDirectLot.id.desc())
                .limit(remaining)
                .all()
            )
            for direct, item, shipment in direct_rows:
                append_result(
                    "OUTBOUND",
                    direct.outbound_lot_no,
                    item.part_no,
                    float(direct.shipped_qty or 0),
                    shipment.shipment_date or "",
                    shipment.shipment_no or "",
                )

    results.sort(key=lambda row: (row["date"], row["lot_no"]), reverse=True)
    return {"search_type": search_type, "keyword": keyword, "total": len(results), "items": results[:limit]}


@router.get("/api/inventory/lot-trace")
def inventory_lot_trace(
    lot_no: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    root = lot_no.strip()
    all_edges = _all_trace_edges(db)
    parent_map: dict[str, list[dict]] = {}
    child_map: dict[str, list[dict]] = {}
    for edge in all_edges:
        parent_map.setdefault(edge["parent_lot_no"], []).append(edge)
        child_map.setdefault(edge["child_lot_no"], []).append(edge)

    queue = deque([root])
    visited: set[str] = set()
    trace_edges: list[dict] = []
    trace_edge_keys: set[tuple] = set()
    while queue and len(visited) < 500:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)

        for edge in parent_map.get(current, []):
            key = (edge["parent_lot_no"], edge["child_lot_no"], edge["process_code"])
            if key not in trace_edge_keys:
                trace_edge_keys.add(key)
                trace_edges.append(dict(edge))
            if edge["child_lot_no"] not in visited:
                queue.append(edge["child_lot_no"])

        for edge in child_map.get(current, []):
            key = (edge["parent_lot_no"], edge["child_lot_no"], edge["process_code"])
            if key not in trace_edge_keys:
                trace_edge_keys.add(key)
                trace_edges.append(dict(edge))
            if edge["parent_lot_no"] not in visited:
                queue.append(edge["parent_lot_no"])

    process_map = _process_name_map(db)
    for edge in trace_edges:
        code = edge["process_code"]
        edge["process_name"] = {
            "PACKING": "포장",
            "SHIPMENT": "출고",
        }.get(code, process_map.get(code, code))

    nodes = [_node_details(db, value) for value in visited]
    nodes.sort(key=lambda row: (0 if row["lot_no"] == root else 1, row["date"], row["lot_no"]))
    trace_edges.sort(key=lambda row: (row["parent_lot_no"], row["child_lot_no"], row["process_code"]))
    return {
        "root_lot_no": root,
        "nodes": nodes,
        "edges": trace_edges,
        "node_count": len(nodes),
        "edge_count": len(trace_edges),
    }
