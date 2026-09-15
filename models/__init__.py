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
from models.partner import Partner, PartnerContact
from models.purchase import PurchaseMaster, PurchaseItem
from models.packing import PackingMaster, PackingLotAllocation, PackingBox


# 테이블 일괄 자동 생성 트리거
Base.metadata.create_all(bind=engine)
from models.purchase_migration import ensure_purchase_entry_columns
from models.subcontract_inbound_migration import ensure_subcontract_inbound_columns
from models.quality_migration import ensure_quality_master_data
from models.production_migration import ensure_production_order_columns
from models.process_reference_migration import ensure_process_code_references
ensure_purchase_entry_columns(engine)
ensure_subcontract_inbound_columns(engine)
ensure_quality_master_data(engine)
ensure_production_order_columns(engine)
ensure_process_code_references(engine)

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
    "QualityInboundResult",
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
