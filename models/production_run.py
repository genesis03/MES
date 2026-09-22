from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','CANCELLED')", name="ck_production_run_status"),
        CheckConstraint("shift_type IN ('DAY','NIGHT')", name="ck_production_run_shift"),
        CheckConstraint("performance_type IN ('MACHINING','ASSEMBLY')", name="ck_production_run_type"),
        CheckConstraint("good_qty >= 0 AND defect_qty >= 0 AND setup_qty >= 0", name="ck_production_run_qty"),
    )

    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("production_work_orders.id"), nullable=False, index=True)
    performance_id = Column(Integer, ForeignKey("production_performances.id"), nullable=True, index=True)
    performance_type = Column(String(20), nullable=False, default="MACHINING", index=True)
    performance_date = Column(String(10), nullable=False, index=True)
    process_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False, index=True)
    operator_id = Column(Integer, ForeignKey("worker_masters.id"), nullable=False, index=True)
    operator_name = Column(String(50), nullable=False)
    equipment_id = Column(Integer, ForeignKey("equipment_masters.id"), nullable=False, index=True)
    equipment_code = Column(String(30), nullable=False)
    equipment_name = Column(String(100), nullable=False)
    shift_type = Column(String(10), nullable=False, index=True)
    start_time = Column(String(16), nullable=False)
    end_time = Column(String(16), nullable=True)
    good_qty = Column(Float, nullable=False, default=0.0)
    defect_qty = Column(Float, nullable=False, default=0.0)
    setup_qty = Column(Float, nullable=False, default=0.0)
    status = Column(String(20), nullable=False, default="IN_PROGRESS", index=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    materials = relationship("ProductionRunMaterial", back_populates="run", cascade="all, delete-orphan", order_by="ProductionRunMaterial.id")
    defects = relationship("ProductionRunDefect", back_populates="run", cascade="all, delete-orphan", order_by="ProductionRunDefect.id")


class ProductionRunMaterial(Base):
    __tablename__ = "production_run_materials"
    __table_args__ = (
        UniqueConstraint("run_id", "material_part_no", name="uq_production_run_material"),
        CheckConstraint("bom_qty > 0 AND required_qty >= 0", name="ck_production_run_material_qty"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False, index=True)
    material_item_id = Column(Integer, ForeignKey("item_master.id"), nullable=True, index=True)
    material_part_no = Column(String(80), nullable=False, index=True)
    material_name = Column(String(200), nullable=True)
    unit = Column(String(10), nullable=False, default="EA")
    bom_qty = Column(Float, nullable=False)
    required_qty = Column(Float, nullable=False, default=0.0)
    sort_order = Column(Integer, nullable=False, default=1)

    run = relationship("ProductionRun", back_populates="materials")
    allocations = relationship("ProductionRunLotAllocation", back_populates="material", cascade="all, delete-orphan", order_by="ProductionRunLotAllocation.id")


class ProductionRunLotAllocation(Base):
    __tablename__ = "production_run_lot_allocations"
    __table_args__ = (
        UniqueConstraint("material_id", "lot_no", name="uq_production_run_material_lot"),
        CheckConstraint("allocated_qty > 0", name="ck_production_run_lot_allocation_qty"),
    )

    id = Column(Integer, primary_key=True)
    material_id = Column(Integer, ForeignKey("production_run_materials.id"), nullable=False, index=True)
    lot_no = Column(String(100), nullable=False, index=True)
    allocated_qty = Column(Float, nullable=False)
    source_type = Column(String(20), nullable=True)
    storage_location = Column(String(20), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    material = relationship("ProductionRunMaterial", back_populates="allocations")


class ProductionRunDefect(Base):
    __tablename__ = "production_run_defects"
    __table_args__ = (
        UniqueConstraint("run_id", "defect_type_code", name="uq_production_run_defect_type"),
        CheckConstraint("defect_qty >= 0", name="ck_production_run_defect_qty"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("production_runs.id"), nullable=False, index=True)
    defect_type_code = Column(String(30), nullable=False, index=True)
    defect_type_name = Column(String(100), nullable=False)
    defect_qty = Column(Float, nullable=False, default=0.0)

    run = relationship("ProductionRun", back_populates="defects")
