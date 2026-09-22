from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, event, text

from core.database import Base


class ItemPartNoHistory(Base):
    """품번 변경 이력.

    item_master.id는 영구 식별자로 유지하고, 사용자가 보는 품번(part_no)이
    변경될 때 이전/신규 품번과 변경 사유를 기록합니다.
    """

    __tablename__ = "item_part_no_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    old_part_no = Column(String(100), nullable=False, index=True)
    new_part_no = Column(String(100), nullable=False, index=True)
    changed_by = Column(String(100), nullable=True)
    reason = Column(Text, nullable=True)
    changed_at = Column(DateTime, nullable=False, default=datetime.now, index=True)


_identity_events_installed = False


def _resolve_item_id(connection, part_no):
    value = str(part_no or "").strip()
    if not value:
        return None
    return connection.execute(
        text("SELECT id FROM item_master WHERE part_no = :part_no LIMIT 1"),
        {"part_no": value},
    ).scalar_one_or_none()


def _identity_listener(pairs):
    def listener(mapper, connection, target):
        for id_attr, part_attr in pairs:
            part_no = getattr(target, part_attr, None)
            if not part_no:
                continue
            resolved_id = _resolve_item_id(connection, part_no)
            if resolved_id is not None:
                setattr(target, id_attr, resolved_id)
    return listener


def install_item_identity_events():
    """기존 라우터를 깨지 않고 신규 업무행의 item_id를 자동 채웁니다."""
    global _identity_events_installed
    if _identity_events_installed:
        return

    from models.models import ManualLabelModel, ItemBomModel, PurchaseOrderItem, PurchaseInboundItem
    from models.purchase import PurchaseItem
    from models.production import ProductionPlan, ProductionWorkOrder
    from models.production_lot import ProductionLotModel
    from models.production_run import ProductionRunMaterial
    from models.lot_consumption import LotConsumptionModel
    from models.quality_standard import QualityInboundStandard
    from models.sales import SalesOrderItem, ShipmentItem, ShipmentDirectLot
    from models.packing import PackingMaster
    from models.shipping_lot import ShippingLotRegistry
    from models.subcontract import SubcontractOrderItem
    from models.subcontract_outbound import SubcontractOutboundItem
    from models.subcontract_inbound import SubcontractInboundItem

    bindings = (
        (ManualLabelModel, (("item_id", "part_no"),)),
        (ItemBomModel, (("parent_item_id", "parent_part_no"), ("child_item_id", "child_part_no"))),
        (PurchaseOrderItem, (("item_id", "part_no"),)),
        (PurchaseInboundItem, (("item_id", "part_no"),)),
        (PurchaseItem, (("item_id", "part_no"),)),
        (ProductionPlan, (("item_id", "part_no"),)),
        (ProductionWorkOrder, (("item_id", "part_no"),)),
        (ProductionLotModel, (("item_id", "part_no"),)),
        (ProductionRunMaterial, (("material_item_id", "material_part_no"),)),
        (LotConsumptionModel, (("item_id", "part_no"),)),
        (QualityInboundStandard, (("item_id", "part_no"),)),
        (SalesOrderItem, (("item_id", "part_no"),)),
        (ShipmentItem, (("item_id", "part_no"),)),
        (ShipmentDirectLot, (("source_item_id", "source_part_no"),)),
        (PackingMaster, (("item_id", "part_no"),)),
        (ShippingLotRegistry, (("item_id", "part_no"),)),
        (SubcontractOrderItem, (("previous_item_id", "previous_part_no"), ("item_id", "order_part_no"))),
        (SubcontractOutboundItem, (("previous_item_id", "previous_part_no"), ("item_id", "order_part_no"))),
        (SubcontractInboundItem, (("previous_item_id", "previous_part_no"), ("item_id", "part_no"))),
    )

    for model, pairs in bindings:
        listener = _identity_listener(pairs)
        event.listen(model, "before_insert", listener)
        event.listen(model, "before_update", listener)

    _identity_events_installed = True
