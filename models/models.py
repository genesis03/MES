from sqlalchemy import Column, Integer, String, Text, Float
from core.database import Base

# [테이블 0] 사용자 계정 및 권한 모델
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="USER")       # "ADMIN" 또는 "USER"
    can_write = Column(Integer, nullable=False, default=0)
    permissions = Column(Text, nullable=True)                  # 계층형 권한 JSON 문자열
    department = Column(String, nullable=True)                  # 사용 부서
    name = Column(String, nullable=False, default="")           # 사용자 성명 (필수)
    note = Column(String, nullable=True)                        # 비고
    created_at = Column(String, nullable=False)

# [테이블 1] 수기 라벨 발행 이력 대장 모델
class ManualLabelModel(Base):
    __tablename__ = "manual_labels"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, nullable=True, index=True)             # 영구 품목 내부키
    created_at = Column(String, nullable=False)
    barcode = Column(String, nullable=False)
    customer = Column(String, nullable=False)
    delivery_date = Column(String, nullable=False)
    part_no = Column(String, nullable=False)
    part_name = Column(String, nullable=False)
    qty = Column(Integer, nullable=False)
    serial = Column(String, nullable=False)

# [테이블 2] 출고 원장 데이터 영구 저장 모델
class ShippingMasterModel(Base):
    __tablename__ = "shipping_master"

    id = Column(Integer, primary_key=True, index=True)
    row_order = Column(Integer, nullable=False)
    row_json = Column(Text, nullable=False)

# [테이블 3] 품목 마스터 (PLM/MES 기준정보)
class ItemMasterModel(Base):
    __tablename__ = "item_master"

    id = Column(Integer, primary_key=True, index=True)
    part_no = Column(String, unique=True, nullable=False, index=True) # 품번 (고유키, 필수)
    vehicle_model = Column(String, nullable=True)                      # 차종 (선택 입력)
    part_name = Column(String, nullable=False)                         # 품명 (필수)
    revision = Column(String, nullable=False, default="Rev.00")        # 설계 리비전
    spec = Column(String, nullable=True)                               # 규격/사양

    account_type = Column(String, nullable=False)                      # 계정유형
    material_type = Column(String, nullable=False)                     # 자재유형
    item_group = Column(String, nullable=True)                         # ITEM (품목군)

    unit = Column(String, nullable=False, default="EA")                # 기본 단위
    snp = Column(Integer, nullable=True, default=0)                    # 표준포장수량
    moq = Column(Integer, nullable=True, default=0)                    # 최소발주수량
    safety_stock = Column(Integer, nullable=True, default=0)           # 안전재고

    weight = Column(Float, nullable=True, default=0.0)                 # 무게 (소수점 3자리)
    inbound_loc = Column(String, nullable=True)                        # 저장위치 (storage_locations 연동)
    production_loc = Column(String, nullable=True)                     # 공정 (processes 연동)

    is_active = Column(String, nullable=False, default="Y")            # 사용여부
    note = Column(Text, nullable=True)                                 # 비고
    extra_data = Column(Text, nullable=True)

    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=True)

# [테이블 4] 일반 공통 코드 모델
class CommonCodeModel(Base):
    __tablename__ = "common_codes"

    id = Column(Integer, primary_key=True, index=True)
    group_code = Column(String, nullable=False, index=True)
    group_name = Column(String, nullable=False)
    code = Column(String, nullable=False)
    code_name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=1)
    is_active = Column(String, nullable=False, default="Y")
    note = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

# [테이블 5] 공정 마스터 모델
class ProcessModel(Base):
    __tablename__ = "processes"

    id = Column(Integer, primary_key=True, index=True)
    process_code = Column(String, unique=True, nullable=False, index=True)
    process_name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=1)
    is_active = Column(String, nullable=False, default="Y")
    note = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

# [테이블 6] 저장위치 마스터 모델
class StorageLocationModel(Base):
    __tablename__ = "storage_locations"

    id = Column(Integer, primary_key=True, index=True)
    location_code = Column(String, unique=True, nullable=False, index=True)
    location_name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=1)
    is_active = Column(String, nullable=False, default="Y")
    note = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

# [테이블 7] 창고 마스터 모델
class WarehouseMasterModel(Base):
    __tablename__ = "warehouse_masters"

    id = Column(Integer, primary_key=True, index=True)
    warehouse_code = Column(String, unique=True, nullable=False, index=True)
    warehouse_name = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=1)
    is_active = Column(String, nullable=False, default="Y")
    note = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

# [테이블 8] 품목 BOM 마스터 모델
class ItemBomModel(Base):
    __tablename__ = "item_boms"

    id = Column(Integer, primary_key=True, index=True)
    parent_item_id = Column(Integer, nullable=True, index=True)
    child_item_id = Column(Integer, nullable=True, index=True)
    parent_part_no = Column(String, nullable=False, index=True)
    child_part_no = Column(String, nullable=False, index=True)
    bom_type = Column(String, nullable=False, default="MFG")
    process_code = Column(String, nullable=True)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String, nullable=False, default="EA")
    loss_rate = Column(Float, nullable=False, default=0.0)
    consumption_type = Column(String, nullable=False, default="AUTO")
    sort_order = Column(Integer, nullable=False, default=1)
    note = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

# 구매 발주 및 입고 모델
from datetime import datetime
from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from core.database import Base


class PurchaseOrderMaster(Base):
    __tablename__ = "purchase_order_masters"
    __table_args__ = (CheckConstraint("status IN ('ORDERED','PARTIAL','COMPLETED','CANCELLED')"),)

    id = Column(Integer, primary_key=True)
    po_no = Column(String(30), unique=True, index=True, nullable=False)
    order_date = Column(String(10), nullable=False)
    delivery_due_date = Column(String(10))
    partner_id = Column(Integer, ForeignKey("partners.id"))
    partner_name = Column(String(100), nullable=False)
    status = Column(String(20), default="ORDERED", nullable=False)
    manager_name = Column(String(50))
    note = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
    items = relationship("PurchaseOrderItem", backref="order", cascade="all, delete-orphan", order_by="PurchaseOrderItem.id")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        CheckConstraint("order_qty > 0 AND received_qty >= 0"),
        CheckConstraint("unit_price >= 0 AND supply_price >= 0 AND vat_price >= 0"),
        CheckConstraint("status IN ('WAITING','PARTIAL','COMPLETED')"),
    )

    id = Column(Integer, primary_key=True)
    po_id = Column(Integer, ForeignKey("purchase_order_masters.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    part_no = Column(String(50), nullable=False)
    order_qty = Column(Float, nullable=False)
    unit = Column(String(10), nullable=False)
    delivery_date = Column(String(10))
    warehouse_code = Column(String(20), nullable=True)
    storage_location = Column(String(20), nullable=True)
    note = Column(Text)
    received_qty = Column(Float, default=0.0, nullable=False)
    unit_price = Column(Float, default=0.0, nullable=False)
    supply_price = Column(Float, default=0.0, nullable=False)
    vat_price = Column(Float, default=0.0, nullable=False)
    status = Column(String(20), default="WAITING", nullable=False)


class PurchaseInboundMaster(Base):
    __tablename__ = "purchase_inbound_masters"

    id = Column(Integer, primary_key=True)
    inbound_no = Column(String(30), unique=True, index=True, nullable=False)
    inbound_date = Column(String(10), nullable=False, index=True)
    partner_id = Column(Integer, index=True)
    partner_name = Column(String(100), nullable=False)
    invoice_no = Column(String(50))
    status = Column(String(20), nullable=False, default="CONFIRMED")
    note = Column(Text)
    created_by = Column(String(50))
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    items = relationship("PurchaseInboundItem", backref="inbound", cascade="all, delete-orphan", order_by="PurchaseInboundItem.id")


class PurchaseInboundItem(Base):
    __tablename__ = "purchase_inbound_items"
    __table_args__ = (
        CheckConstraint("inbound_qty > 0 AND unit_price >= 0"),
        CheckConstraint("length(trim(supplier_lot_no)) > 0"),
    )

    id = Column(Integer, primary_key=True)
    inbound_id = Column(Integer, ForeignKey("purchase_inbound_masters.id"), nullable=False, index=True)
    po_item_id = Column(Integer, ForeignKey("purchase_order_items.id"), nullable=True, index=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    part_no = Column(String(50), nullable=False, index=True)
    unit = Column(String(10), nullable=False)
    inspection_status = Column(String(20), nullable=False, default="WAITING")
    inbound_qty = Column(Float, nullable=False)
    supplier_lot_no = Column(String(100), nullable=False, index=True)
    internal_lot_no = Column(String(100), index=True)
    note = Column(Text)
    warehouse_code = Column(String(20), ForeignKey("warehouse_masters.warehouse_code"), nullable=True)
    storage_location = Column(String(20), ForeignKey("storage_locations.location_code"), nullable=False)
    unit_price = Column(Float, default=0.0, nullable=False)