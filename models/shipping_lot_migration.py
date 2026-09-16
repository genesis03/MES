from sqlalchemy.orm import Session

from models.packing import PackingBox, PackingMaster
from models.sales import ShipmentDirectLot, ShipmentItem, ShipmentMaster
from models.shipping_lot import ShippingLotRegistry


def _parse_shipping_lot(lot_no: str | None):
    text = str(lot_no or "").strip()
    if len(text) != 11 or not text.isdigit() or text[6:8] != "01":
        return None
    return text[:6], int(text[-3:])


def ensure_shipping_lot_registry(engine) -> None:
    """기존 포장/직출고 LOT를 공용 레지스트리에 보강합니다."""
    db = Session(bind=engine)
    try:
        existing = {
            (row.part_no, row.lot_no)
            for row in db.query(ShippingLotRegistry.part_no, ShippingLotRegistry.lot_no).all()
        }

        packing_rows = (
            db.query(PackingMaster.part_no, PackingMaster.packing_date, PackingBox.package_lot_no)
            .join(PackingBox, PackingBox.packing_id == PackingMaster.id)
            .all()
        )
        for part_no, packing_date, lot_no in packing_rows:
            parsed = _parse_shipping_lot(lot_no)
            key = (part_no, lot_no)
            if not parsed or key in existing:
                continue
            _, sequence = parsed
            db.add(ShippingLotRegistry(
                part_no=part_no,
                lot_no=lot_no,
                lot_date=packing_date,
                sequence=sequence,
                source_type="PACKING",
            ))
            existing.add(key)

        direct_rows = (
            db.query(
                ShipmentItem.part_no,
                ShipmentMaster.shipment_date,
                ShipmentDirectLot.outbound_lot_no,
            )
            .join(ShipmentDirectLot, ShipmentDirectLot.shipment_item_id == ShipmentItem.id)
            .join(ShipmentMaster, ShipmentMaster.id == ShipmentItem.shipment_id)
            .filter(ShipmentDirectLot.outbound_lot_no.isnot(None))
            .all()
        )
        for part_no, shipment_date, lot_no in direct_rows:
            parsed = _parse_shipping_lot(lot_no)
            key = (part_no, lot_no)
            if not parsed or key in existing:
                continue
            _, sequence = parsed
            db.add(ShippingLotRegistry(
                part_no=part_no,
                lot_no=lot_no,
                lot_date=shipment_date,
                sequence=sequence,
                source_type="DIRECT",
            ))
            existing.add(key)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
