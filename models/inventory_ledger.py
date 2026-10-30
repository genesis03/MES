"""Reconciliation worksheet data. Saving never mutates operational LOT stock."""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Numeric, UniqueConstraint, Boolean
from core.database import Base


class InventoryLedgerBalance(Base):
    __tablename__ = 'inventory_ledger_balances'
    __table_args__ = (UniqueConstraint('month', 'source', 'item_id'),)
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)
    source = Column(String(10), nullable=False)
    item_id = Column(Integer, ForeignKey('item_master.id'), nullable=False)
    opening_qty = Column(Numeric(20, 6))
    actual_qty = Column(Numeric(20, 6))
    count_carry_qty = Column(Numeric(20, 6), nullable=False, default=0)
    count_date = Column(String(10))
    note = Column(Text, nullable=False, default='')
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_by = Column(String(100), nullable=False)


class InventoryLedgerEntry(Base):
    __tablename__ = 'inventory_ledger_entries'
    id = Column(Integer, primary_key=True)
    source = Column(String(10), nullable=False, index=True)
    entry_date = Column(String(10), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey('item_master.id'), nullable=False)
    direction = Column(String(3), nullable=False)
    category = Column(String(30), nullable=False)
    qty = Column(Numeric(20, 6), nullable=False)
    document_no = Column(String(100), nullable=False, default='')
    lot_no = Column(String(100), nullable=False, default='')
    reason = Column(Text, nullable=False)
    voided = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    created_by = Column(String(100), nullable=False)


class InventoryLedgerAudit(Base):
    __tablename__ = 'inventory_ledger_audits'
    id = Column(Integer, primary_key=True)
    month = Column(String(7), nullable=False, index=True)
    source = Column(String(10), nullable=False)
    item_id = Column(Integer, ForeignKey('item_master.id'), nullable=False)
    action = Column(String(30), nullable=False)
    before_json = Column(Text, nullable=False)
    after_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    created_by = Column(String(100), nullable=False)
