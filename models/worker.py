from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from core.database import Base


class WorkerMaster(Base):
    __tablename__ = "worker_masters"

    id = Column(Integer, primary_key=True)
    worker_code = Column(String(30), unique=True, nullable=False, index=True)
    worker_name = Column(String(50), nullable=False, index=True)
    department = Column(String(50), nullable=True)
    is_active = Column(String(1), nullable=False, default="Y", index=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    processes = relationship(
        "WorkerProcess",
        back_populates="worker",
        cascade="all, delete-orphan",
        order_by="WorkerProcess.id",
    )


class WorkerProcess(Base):
    __tablename__ = "worker_processes"
    __table_args__ = (
        UniqueConstraint("worker_id", "process_code", name="uq_worker_process"),
    )

    id = Column(Integer, primary_key=True)
    worker_id = Column(Integer, ForeignKey("worker_masters.id"), nullable=False, index=True)
    process_code = Column(String(50), ForeignKey("processes.process_code"), nullable=False, index=True)

    worker = relationship("WorkerMaster", back_populates="processes")
