from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from core.database import Base


class EquipmentMaster(Base):
    __tablename__ = "equipment_masters"
    __table_args__ = (
        UniqueConstraint("process_code", "machine_no", name="uq_equipment_process_machine_no"),
    )

    id = Column(Integer, primary_key=True)
    equipment_code = Column(String(30), unique=True, nullable=False, index=True)
    equipment_name = Column(String(100), nullable=False, index=True)
    process_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False, index=True)
    machine_no = Column(String(20), nullable=False)
    is_active = Column(String(1), nullable=False, default="Y", index=True)
    sort_order = Column(Integer, nullable=False, default=1)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
