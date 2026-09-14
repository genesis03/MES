from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from core.database import Base


class ProductionPlan(Base):
    __tablename__ = "production_plans"
    __table_args__ = (
        CheckConstraint("plan_qty > 0", name="ck_production_plan_qty_positive"),
    )

    id = Column(Integer, primary_key=True)
    plan_date = Column(String(10), nullable=False, index=True)
    part_no = Column(String(50), ForeignKey("item_master.part_no"), nullable=False, index=True)
    plan_qty = Column(Float, nullable=False)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    work_orders = relationship("ProductionWorkOrder", back_populates="plan")


class ProductionWorkOrder(Base):
    __tablename__ = "production_work_orders"
    __table_args__ = (
        CheckConstraint("order_qty > 0", name="ck_production_order_qty_positive"),
        CheckConstraint("production_qty >= 0", name="ck_production_qty_nonnegative"),
        CheckConstraint(
            "status IN ('WAITING','IN_PROGRESS','COMPLETED','CANCELLED')",
            name="ck_production_order_status",
        ),
    )

    id = Column(Integer, primary_key=True)
    work_order_no = Column(String(30), unique=True, nullable=False, index=True)
    order_date = Column(String(10), nullable=False, index=True)
    scheduled_date = Column(String(10), nullable=True, index=True)
    plan_id = Column(Integer, ForeignKey("production_plans.id"), nullable=True, index=True)
    part_no = Column(String(50), ForeignKey("item_master.part_no"), nullable=False, index=True)
    order_qty = Column(Float, nullable=False)
    production_qty = Column(Float, nullable=False, default=0.0)
    status = Column(String(20), nullable=False, default="WAITING", index=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    plan = relationship("ProductionPlan", back_populates="work_orders")
    performances = relationship("ProductionPerformance", back_populates="work_order")


class ProductionPerformance(Base):
    __tablename__ = "production_performances"
    __table_args__ = (
        CheckConstraint("good_qty > 0", name="ck_production_performance_good_qty_positive"),
        CheckConstraint("defect_qty >= 0", name="ck_production_performance_defect_qty_nonnegative"),
        CheckConstraint(
            "performance_type IN ('MACHINING','ASSEMBLY')",
            name="ck_production_performance_type",
        ),
    )

    id = Column(Integer, primary_key=True)
    work_order_id = Column(Integer, ForeignKey("production_work_orders.id"), nullable=False, index=True)
    performance_type = Column(String(20), nullable=False, default="MACHINING", index=True)
    performance_date = Column(String(10), nullable=False, index=True)
    process_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False, index=True)
    good_qty = Column(Float, nullable=False)
    defect_qty = Column(Float, nullable=False, default=0.0)
    operator_name = Column(String(50), nullable=True)
    note = Column(Text, nullable=True)
    created_by = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    work_order = relationship("ProductionWorkOrder", back_populates="performances")
