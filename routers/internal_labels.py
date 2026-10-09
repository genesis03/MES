from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from core.database import get_db
from core.security import get_current_user
from models.models import ItemMasterModel, ProcessModel, PurchaseInboundMaster, StorageLocationModel
from models.packing import PackingMaster
from models.production import ProductionPerformance
from models.production_lot import ProductionLotModel
from models.sales import ShipmentMaster
from models.subcontract_inbound import SubcontractInboundMaster
from models.subcontract_outbound import SubcontractOutboundMaster

router = APIRouter(tags=["Internal LOT Labels"])
templates = Jinja2Templates(directory="templates")


def _item(db: Session, part_no: str):
    return db.query(ItemMasterModel).filter(ItemMasterModel.part_no == part_no).first()


def _item_by_id(db: Session, item_id: int | None):
    return db.get(ItemMasterModel, item_id) if item_id else None


def _storage_text(db: Session, code: str | None) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    row = db.query(StorageLocationModel).filter(StorageLocationModel.location_code == raw).first()
    return str(row.location_name or "").strip() if row else ""


def _process_text(db: Session, code: str | None, fallback: str = "") -> str:
    raw = str(code or "").strip()
    if raw:
        row = db.query(ProcessModel).filter(ProcessModel.process_code == raw).first()
        if row and row.process_name:
            return str(row.process_name).strip()
    return str(fallback or "").strip()


def _label(
    *, title: str, part_no: str, part_name: str, lot_no: str, qty: float,
    unit: str, date: str, process: str, storage: str, extras: list[dict] | None = None,
    date_label: str = "기준일", operator: str = "", shift: str = "",
    equipment: str = "", compact_production: bool = False,
) -> dict:
    return {
        "title": title,
        "part_no": part_no or "",
        "part_name": part_name or "",
        "lot_no": lot_no or "",
        "qty": float(qty or 0),
        "unit": unit or "EA",
        "date": date or "",
        "date_label": date_label or "기준일",
        "process": process or "",
        "storage": storage or "",
        "operator": operator or "",
        "shift": shift or "",
        "equipment": equipment or "",
        "compact_production": bool(compact_production),
        "extras": extras or [],
    }


def _purchase_labels(db: Session, inbound_id: int) -> list[dict]:
    master = db.get(PurchaseInboundMaster, inbound_id)
    if not master or master.status != "CONFIRMED":
        raise HTTPException(409, "입고 확정된 구매 건만 라벨을 출력할 수 있습니다.")
    result = []
    for row in master.items:
        lot_no = str(row.internal_lot_no or "").strip()
        if not lot_no:
            continue
        item = _item_by_id(db, row.item_id) or _item(db, row.part_no)
        result.append(_label(
            title="자재 LOT",
            part_no=item.part_no if item else row.part_no,
            part_name=item.part_name if item else "",
            lot_no=lot_no,
            qty=row.inbound_qty,
            unit=row.unit or "EA",
            date=master.inbound_date,
            process="구매입고",
            storage=_storage_text(db, row.storage_location),
        ))
    return result


def _production_labels(db: Session, performance_id: int) -> list[dict]:
    performance = db.get(ProductionPerformance, performance_id)
    if not performance or not performance.work_order:
        raise HTTPException(404, "생산실적을 찾을 수 없습니다.")
    lot = (
        db.query(ProductionLotModel)
        .filter(ProductionLotModel.note.like(f"PERF:{performance.id}%"))
        .first()
    )
    if not lot:
        raise HTTPException(409, "생산 LOT가 생성되지 않은 실적입니다.")
    order = performance.work_order
    item = _item_by_id(db, order.item_id) or _item(db, order.part_no)
    is_assembly = str(performance.performance_type or "").upper() == "ASSEMBLY"
    good = float(performance.good_qty or 0)
    defect = float(performance.defect_qty or 0)
    setup = float(performance.setup_qty or 0)
    production = good + defect + setup
    return [_label(
        title="조립 LOT" if is_assembly else "생산 LOT",
        part_no=item.part_no if item else order.part_no,
        part_name=item.part_name if item else "",
        lot_no=lot.lot_no,
        qty=good,
        unit=(item.unit if item else "EA") or "EA",
        date=performance.performance_date,
        date_label="생산일",
        process=_process_text(db, performance.process_code, "조립" if is_assembly else "가공"),
        storage=_storage_text(db, lot.storage_location),
        operator=str(performance.operator_name or "").strip(),
        equipment=str(performance.equipment_name or performance.equipment_code or "").strip(),
        shift={"DAY": "주간", "NIGHT": "야간"}.get(str(performance.shift_type or "").strip().upper(), str(performance.shift_type or "").strip()),
        compact_production=True,
        extras=[
            {"label": "양품", "value": good},
            {"label": "불량", "value": defect},
            {"label": "SET-UP", "value": setup},
            {"label": "총생산", "value": production},
        ],
    )]


def _subcontract_outbound_labels(db: Session, outbound_id: int) -> list[dict]:
    master = db.get(SubcontractOutboundMaster, outbound_id)
    if not master or master.status != "OUTBOUND":
        raise HTTPException(409, "출고완료 상태의 외주가공 출고 건만 라벨을 출력할 수 있습니다.")
    result = []
    for item_row in master.items:
        source_item = _item_by_id(db, item_row.previous_item_id) or _item(db, item_row.previous_part_no)
        for lot in item_row.lots:
            result.append(_label(
                title="외주 이동 LOT",
                part_no=source_item.part_no if source_item else item_row.previous_part_no,
                part_name=source_item.part_name if source_item else item_row.order_part_name,
                lot_no=lot.lot_no,
                qty=lot.outbound_qty,
                unit=item_row.unit or "EA",
                date=master.outbound_date,
                process=master.processing_type_name,
                storage=_storage_text(db, master.external_storage_location),
            ))
    return result


def _subcontract_inbound_labels(db: Session, inbound_id: int) -> list[dict]:
    master = db.get(SubcontractInboundMaster, inbound_id)
    if not master or master.status != "RECEIVED":
        raise HTTPException(409, "입고완료 상태의 외주가공 입고 건만 라벨을 출력할 수 있습니다.")
    result = []
    for item_row in master.items:
        inbound_item = _item_by_id(db, item_row.item_id) or _item(db, item_row.part_no)
        for lot in item_row.lots:
            lot_no = str(lot.child_lot_no or lot.source_lot_no or "").strip()
            if not lot_no:
                continue
            result.append(_label(
                title="외주가공 LOT",
                part_no=inbound_item.part_no if inbound_item else item_row.part_no,
                part_name=inbound_item.part_name if inbound_item else item_row.part_name,
                lot_no=lot_no,
                qty=lot.good_qty,
                unit=item_row.unit or "EA",
                date=master.inbound_date,
                process=master.processing_type_name,
                storage=_storage_text(db, master.storage_location),
            ))
    return result


def _packing_labels(db: Session, packing_id: int, lot_no: str | None = None) -> list[dict]:
    master = db.get(PackingMaster, packing_id)
    if not master or master.status != "PACKED":
        raise HTTPException(409, "포장완료 상태의 건만 라벨을 출력할 수 있습니다.")
    result = []
    for box in master.boxes:
        if lot_no and str(box.package_lot_no or "").strip() != lot_no.strip():
            continue
        result.append(_label(
            title="출고 대기",
            part_no=master.part_no,
            part_name=master.part_name or "",
            lot_no=box.package_lot_no,
            qty=box.box_qty,
            unit="EA",
            date=master.packing_date,
            process="포장",
            storage="출고대기",
        ))
    return result


def _direct_shipment_labels(db: Session, shipment_id: int) -> list[dict]:
    shipment = db.get(ShipmentMaster, shipment_id)
    if not shipment or shipment.status != "CONFIRMED":
        raise HTTPException(409, "확정된 출고 건만 내부 BOX 라벨을 출력할 수 있습니다.")
    result = []
    for item_row in shipment.items:
        direct_rows = list(getattr(item_row, "direct_lots", []) or [])
        if not direct_rows:
            continue
        sales_item = item_row.sales_order_item
        sales_order = sales_item.order if sales_item else None
        order_type = str(getattr(sales_order, "order_type", "") or "").upper()
        process = "샘플 직출고" if order_type == "SAMPLE" else ("개발 직출고" if order_type == "DEVELOPMENT" else "직출고")
        grouped: dict[tuple[int, str], float] = {}
        for row in direct_rows:
            key = (int(getattr(row, "box_no", 0) or 0), str(row.outbound_lot_no or "").strip())
            grouped[key] = grouped.get(key, 0.0) + float(row.shipped_qty or 0)
        for (_, outbound_lot_no), qty in sorted(grouped.items()):
            if not outbound_lot_no:
                continue
            result.append(_label(
                title="출고 대기",
                part_no=item_row.part_no,
                part_name=sales_item.part_name if sales_item else "",
                lot_no=outbound_lot_no,
                qty=qty,
                unit=item_row.unit or "EA",
                date=shipment.shipment_date,
                process=process,
                storage="출고대기",
            ))
    return result


def _external_labels(db: Session, record_id: int, kind: str, connections=None) -> list[dict]:
    from models.production_sync import ExternalProductionRecord
    from models.packing_sync import ExternalPackingRecord
    from services.production_sync_mapping import ItemConnections
    from services.production_sync_view import external_record_view
    from services.packing_sync_view import packing_record_view

    model = ExternalProductionRecord if kind == 'production' else ExternalPackingRecord
    record = db.get(model, record_id)
    if not record:
        raise HTTPException(404, '외부 실적을 찾을 수 없습니다.')
    connections = connections or ItemConnections(db)
    view = external_record_view(record, connections) if kind == 'production' else packing_record_view(record, connections)
    if not view['can_print_label']:
        raise HTTPException(409, view['label_error'])
    item, _ = connections.resolve(record.part_no, record.process_name if kind == 'production' else '포장')
    production = kind == 'production'
    extras = [{'label': label, 'value': view[key]} for label, key in
              [('양품', 'good_qty'), ('불량', 'fault_qty'), ('SET-UP', 'setup_qty'), ('총생산', 'total_qty')]] if production else []
    return [_label(
        title=('조립 LOT' if view['performance_type'] == 'ASSEMBLY' else '생산 LOT') if production else '출고 대기',
        part_no=item.part_no, part_name=item.part_name, lot_no=record.lot_no.strip(),
        qty=float(view['good_qty'] if production else record.packing_qty), unit=item.unit or 'EA',
        date=record.work_date if production else record.packing_date,
        date_label='생산일' if production else '포장일',
        process=(view['process_name'] or record.process_name) if production else '포장',
        storage=_storage_text(db, item.inbound_loc) or item.inbound_loc or '',
        equipment=view['machine_no'] + '호기' if production and view['machine_no'] else '',
        compact_production=production, extras=extras,
    )]


@router.get('/internal-labels/packing-waiting', response_class=HTMLResponse)
def print_waiting_packing_labels(
    request: Request, part_no: str = Query(..., min_length=1, max_length=50),
    lot_no: str | None = Query(None, max_length=100), auto: int = Query(1, ge=0, le=1),
    db: Session = Depends(get_db), current_user=Depends(get_current_user),
):
    from routers.packing import packing_records
    from services.production_sync_mapping import ItemConnections

    connections = ItemConnections(db)
    records = packing_records(part_no=part_no, db=db, current_user=current_user)
    labels = []
    for record in records:
        for box in record['waiting_boxes']:
            if lot_no is not None and box['package_lot_no'] != lot_no:
                continue
            if record['record_source'] == 'EXTERNAL':
                labels.extend(_external_labels(db, record['source_record_id'], 'packing', connections))
            else:
                labels.extend(_packing_labels(db, record['id'], box['package_lot_no']))
    if not labels:
        raise HTTPException(404, '출력할 출고 대기 LOT가 없습니다.')
    return templates.TemplateResponse(request=request, name='internal_label_print.html',
        context={'request': request, 'user': current_user, 'labels': labels, 'auto_print': bool(auto)})


@router.get("/internal-labels/{source}/{record_id}", response_class=HTMLResponse)
def print_internal_labels(
    source: str,
    record_id: int,
    request: Request,
    lot_no: str | None = Query(None, max_length=100),
    auto: int = Query(1, ge=0, le=1),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    builders = {
        "external-production": lambda: _external_labels(db, record_id, "production"),
        "external-packing": lambda: _external_labels(db, record_id, "packing"),
        "purchase": lambda: _purchase_labels(db, record_id),
        "production": lambda: _production_labels(db, record_id),
        "subcontract-outbound": lambda: _subcontract_outbound_labels(db, record_id),
        "subcontract-inbound": lambda: _subcontract_inbound_labels(db, record_id),
        "packing": lambda: _packing_labels(db, record_id, lot_no),
        "direct-shipment": lambda: _direct_shipment_labels(db, record_id),
    }
    builder = builders.get(source)
    if not builder:
        raise HTTPException(404, "지원하지 않는 내부 라벨 출력 유형입니다.")
    labels = builder()
    if not labels:
        raise HTTPException(404, "출력할 내부 LOT 라벨이 없습니다.")
    return templates.TemplateResponse(
        request=request,
        name="internal_label_print.html",
        context={
            "request": request,
            "user": current_user,
            "labels": labels,
            "auto_print": bool(auto),
        },
    )
