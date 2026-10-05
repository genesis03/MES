"""입고/공정/최종 검사기준서 공통 개정 이력 모델."""
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from core.database import Base


class InspectionStandard(Base):
    __tablename__ = "inspection_standards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_type = Column(String(20), nullable=False, index=True)  # INBOUND / PROCESS / FINAL
    item_id = Column(Integer, ForeignKey("item_master.id", ondelete="RESTRICT"), nullable=False, index=True)
    process_flow_step_key_id = Column(
        Integer, ForeignKey("process_flow_step_keys.id", ondelete="RESTRICT"), nullable=True, index=True
    )

    revision = Column(String(20), nullable=False)
    sequence = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="DRAFT", index=True)  # DRAFT/CURRENT/SUPERSEDED/RETIRED
    previous_revision_id = Column(
        Integer, ForeignKey("inspection_standards.id", ondelete="RESTRICT"), nullable=True
    )

    management_no = Column(String(80), nullable=True)
    effective_date = Column(String(10), nullable=True)
    change_summary = Column(Text, nullable=True)
    change_reason = Column(Text, nullable=True)
    note = Column(Text, nullable=True)

    part_no_snapshot = Column(String(100), nullable=False)
    part_name_snapshot = Column(String(200), nullable=False, default="")
    process_step_no_snapshot = Column(String(50), nullable=True)
    process_step_name_snapshot = Column(String(200), nullable=True)

    prepared_by = Column(String(100), nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    approved_by = Column(String(100), nullable=True)

    # 향후 관리계획서 연동용. 현재는 연결하지 않고 출처 식별값만 보관할 수 있게 둡니다.
    control_plan_revision_id = Column(Integer, nullable=True)
    control_plan_process_no = Column(String(50), nullable=True)
    control_plan_item_key = Column(String(100), nullable=True)

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    activated_at = Column(DateTime, nullable=True)
    superseded_at = Column(DateTime, nullable=True)
    retired_at = Column(DateTime, nullable=True)

    prechecks = relationship(
        "InspectionStandardPrecheck",
        back_populates="standard",
        cascade="all, delete-orphan",
        order_by="InspectionStandardPrecheck.sort_order, InspectionStandardPrecheck.id",
    )
    items = relationship(
        "InspectionStandardItem",
        back_populates="standard",
        cascade="all, delete-orphan",
        order_by="InspectionStandardItem.sort_order, InspectionStandardItem.id",
    )


class InspectionStandardPrecheck(Base):
    __tablename__ = "inspection_standard_prechecks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_id = Column(
        Integer, ForeignKey("inspection_standards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sort_order = Column(Integer, nullable=False, default=1)
    check_item = Column(String(200), nullable=False)
    criteria = Column(Text, nullable=True)
    responsible = Column(String(100), nullable=True)
    frequency = Column(String(100), nullable=True)
    abnormal_action = Column(Text, nullable=True)
    note = Column(Text, nullable=True)

    standard = relationship("InspectionStandard", back_populates="prechecks")


class InspectionStandardItem(Base):
    __tablename__ = "inspection_standard_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    standard_id = Column(
        Integer, ForeignKey("inspection_standards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sort_order = Column(Integer, nullable=False, default=1)
    inspection_group_no = Column(String(30), nullable=True)
    inspection_item_name = Column(String(200), nullable=False)
    detail_no = Column(String(50), nullable=True)
    special_characteristic = Column(String(50), nullable=True)

    inspection_tool = Column(String(200), nullable=True)
    spec_text = Column(Text, nullable=True)
    nominal_value = Column(Float, nullable=True)
    lower_limit = Column(Float, nullable=True)
    upper_limit = Column(Float, nullable=True)
    unit = Column(String(30), nullable=True)

    inspection_frequency = Column(String(100), nullable=True)
    sample_qty_text = Column(String(100), nullable=True)
    record_management = Column(String(200), nullable=True)
    note = Column(Text, nullable=True)

    standard = relationship("InspectionStandard", back_populates="items")
