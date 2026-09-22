from collections import deque

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel, PurchaseInboundItem, PurchaseInboundMaster
from models.production_lot import ProductionLotModel
from routers.inventory_lot_trace_tree import _edges, _node, _process_map

router = APIRouter(tags=["Inventory Lot Usage Trace"])
templates = Jinja2Templates(directory="templates")


@router.get("/inventory/lot-usage-trace", response_class=HTMLResponse)
def inventory_lot_usage_trace_page(request: Request, current_user=Depends(get_current_user)):
    return templates.TemplateResponse(
        request=request,
        name="inventory_lot_usage_trace.html",
        context={"request": request, "user": current_user},
    )


@router.get("/api/inventory/lot-usage/search")
def inventory_lot_usage_search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    keyword = q.strip()
    results: list[dict] = []
    seen: set[str] = set()

    def add(lot_no: str, item_id: int | None, part_no: str, qty: float, date: str, kind: str):
        lot_no = str(lot_no or "").strip()
        if not lot_no or lot_no in seen:
            return
        seen.add(lot_no)
        item = db.get(ItemMasterModel, item_id) if item_id else None
        results.append({
            "lot_no": lot_no,
            "item_id": item_id,
            "part_no": item.part_no if item else (part_no or ""),
            "qty": float(qty or 0),
            "date": date or "",
            "kind": kind,
        })

    purchases = (
        db.query(PurchaseInboundItem, PurchaseInboundMaster)
        .join(PurchaseInboundMaster, PurchaseInboundMaster.id == PurchaseInboundItem.inbound_id)
        .filter(
            PurchaseInboundMaster.status == "CONFIRMED",
            PurchaseInboundItem.internal_lot_no.contains(keyword, autoescape=True),
        )
        .order_by(PurchaseInboundMaster.created_at.desc(), PurchaseInboundItem.id.desc())
        .limit(limit)
        .all()
    )
    for item, master in purchases:
        add(item.internal_lot_no, item.item_id, item.part_no, item.inbound_qty, master.inbound_date, "구매입고")

    remaining = max(limit - len(results), 0)
    if remaining:
        productions = (
            db.query(ProductionLotModel)
            .filter(ProductionLotModel.lot_no.contains(keyword, autoescape=True))
            .order_by(ProductionLotModel.created_at.desc(), ProductionLotModel.id.desc())
            .limit(remaining)
            .all()
        )
        for lot in productions:
            add(
                lot.lot_no,
                lot.item_id,
                lot.part_no,
                lot.lot_qty,
                lot.created_at.strftime("%Y-%m-%d") if lot.created_at else "",
                "공정 LOT",
            )

    results.sort(key=lambda row: (row["date"], row["lot_no"]), reverse=True)
    return {"items": results[:limit], "total": len(results)}


@router.get("/api/inventory/lot-usage-trace")
def inventory_lot_usage_trace(
    lot_no: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    root = lot_no.strip()
    all_edges = _edges(db)

    child_edges: dict[str, list[dict]] = {}
    for edge in all_edges:
        child_edges.setdefault(edge["parent_lot_no"], []).append(edge)

    process_map = _process_map(db)
    node_cache: dict[str, dict] = {}

    def node(value: str) -> dict:
        if value not in node_cache:
            node_cache[value] = _node(db, value, process_map)
        return node_cache[value]

    rows: list[dict] = []
    queue = deque([(root, [root])])
    visited_paths: set[tuple[str, ...]] = set()

    while queue and len(rows) < 500:
        current, path = queue.popleft()
        outgoing = child_edges.get(current, [])

        for edge in outgoing:
            used_lot = edge["child_lot_no"]
            if used_lot in path:
                continue
            source_node = node(current)
            used_node = node(used_lot)
            next_path = path + [used_lot]
            path_key = tuple(next_path)
            if path_key in visited_paths:
                continue
            visited_paths.add(path_key)

            rows.append({
                "process": used_node["process_name"],
                "source_lot_no": current,
                "source_date": source_node["date"],
                "source_part_no": source_node["part_no"],
                "used_lot_no": used_lot,
                "used_lot_qty": used_node["qty"],
                "used_part_no": used_node["part_no"],
                "used_qty": float(edge.get("qty") or 0),
                "tree": " - ".join(next_path),
            })
            queue.append((used_lot, next_path))

    return {
        "root_lot_no": root,
        "root": node(root),
        "rows": rows,
        "row_count": len(rows),
        "lot_count": len({root} | {row["used_lot_no"] for row in rows}),
    }
