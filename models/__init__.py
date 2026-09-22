from core.database import Base, engine
from models.models import (
    PurchaseOrderMaster, PurchaseOrderItem, PurchaseInboundMaster, PurchaseInboundItem,
    UserModel,
    ManualLabelModel,
    ShippingMasterModel,
    ItemMasterModel,
    CommonCodeModel,
    ProcessModel,
    StorageLocationModel,
    WarehouseMasterModel,
    ItemBomModel,
)
from models.lot_relation import LotRelationModel
from models.lot_consumption import LotConsumptionModel
from models.production_lot import ProductionLotModel
from models.production import ProductionPlan, ProductionWorkOrder, ProductionPerformance
from models.production_run import ProductionRun, ProductionRunMaterial, ProductionRunLotAllocation, ProductionRunDefect
from models.worker import WorkerMaster, WorkerProcess
from models.equipment import EquipmentMaster
from models.subcontract import SubcontractOrderMaster, SubcontractOrderItem, SubcontractLotAllocation
from models.subcontract_outbound import SubcontractOutboundMaster, SubcontractOutboundItem, SubcontractOutboundLot
from models.subcontract_inbound import SubcontractInboundMaster, SubcontractInboundItem, SubcontractInboundLot
from models.quality import QualityInboundResult
from models.quality_standard import QualityInspectionItemMaster, QualityInboundStandard, QualityInboundStandardItem
from models.partner import Partner, PartnerContact
from models.purchase import PurchaseMaster, PurchaseItem
from models.packing import PackingMaster, PackingLotAllocation, PackingBox
from models.sales import SalesOrderMaster, SalesOrderItem, ShipmentMaster, ShipmentItem, ShipmentBox, ShipmentDirectLot
from models.shipping_lot import ShippingLotRegistry
from models.item_identity import ItemPartNoHistory, install_item_identity_events


# 신규/수정 업무행은 품번과 함께 영구 item_id를 자동 연결합니다.
install_item_identity_events()

# 테이블 일괄 자동 생성 트리거
Base.metadata.create_all(bind=engine)
from models.purchase_migration import ensure_purchase_entry_columns
from models.subcontract_inbound_migration import ensure_subcontract_inbound_columns
from models.quality_migration import ensure_quality_master_data
from models.production_migration import ensure_production_order_columns
from models.process_reference_migration import ensure_process_code_references
from models.sales_migration import ensure_sales_policy_columns
from models.packing_migration import ensure_packing_lot_scope
from models.shipping_lot_migration import ensure_shipping_lot_registry
from models.item_identity_migration import ensure_item_identity_columns
ensure_purchase_entry_columns(engine)
ensure_subcontract_inbound_columns(engine)
ensure_quality_master_data(engine)
ensure_production_order_columns(engine)
ensure_process_code_references(engine)
ensure_sales_policy_columns(engine)
ensure_packing_lot_scope(engine)
ensure_shipping_lot_registry(engine)
ensure_item_identity_columns(engine)

__all__ = [
    "PurchaseOrderMaster", "PurchaseOrderItem", "PurchaseInboundMaster", "PurchaseInboundItem",
    "LotRelationModel", "LotConsumptionModel",
    "ProductionLotModel",
    "ProductionPlan", "ProductionWorkOrder", "ProductionPerformance",
    "ProductionRun", "ProductionRunMaterial", "ProductionRunLotAllocation", "ProductionRunDefect",
    "WorkerMaster", "WorkerProcess",
    "EquipmentMaster",
    "SubcontractOrderMaster", "SubcontractOrderItem", "SubcontractLotAllocation",
    "SubcontractOutboundMaster", "SubcontractOutboundItem", "SubcontractOutboundLot",
    "SubcontractInboundMaster", "SubcontractInboundItem", "SubcontractInboundLot",
    "PackingMaster", "PackingLotAllocation", "PackingBox",
    "SalesOrderMaster", "SalesOrderItem", "ShipmentMaster", "ShipmentItem", "ShipmentBox", "ShipmentDirectLot",
    "ShippingLotRegistry", "ItemPartNoHistory",
    "QualityInboundResult",
    "QualityInspectionItemMaster", "QualityInboundStandard", "QualityInboundStandardItem",
    "Base",
    "engine",
    "UserModel",
    "ManualLabelModel",
    "ShippingMasterModel",
    "ItemMasterModel",
    "CommonCodeModel",
    "ProcessModel",
    "StorageLocationModel",
    "WarehouseMasterModel",
    "ItemBomModel",
    "Partner",
    "PartnerContact",
    "PurchaseMaster",
    "PurchaseItem",
]
