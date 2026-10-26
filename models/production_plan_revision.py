from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, UniqueConstraint
from core.database import Base


class ProductionPlanRevisionState(Base):
    __tablename__='production_plan_revision_states'
    month=Column(String(7),primary_key=True)
    applied_revision=Column(Integer,nullable=False,default=0)
    generation=Column(Integer,nullable=False,default=0)
    saved_by=Column(String(100))
    saved_at=Column(DateTime)


class ProductionPlanRevision(Base):
    __tablename__='production_plan_revisions'
    __table_args__=(UniqueConstraint('month','number',name='uq_plan_revision_month_number'),)
    id=Column(Integer,primary_key=True)
    month=Column(String(7),ForeignKey('production_plan_revision_states.month'),nullable=False,index=True)
    number=Column(Integer,nullable=False)
    snapshot_json=Column(Text,nullable=False)
    reason=Column(Text,nullable=False)
    created_by=Column(String(100),nullable=False)
    created_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ProductionPlanRevisionReference(Base):
    __tablename__='production_plan_revision_references'
    id=Column(Integer,primary_key=True)
    revision_id=Column(Integer,ForeignKey('production_plan_revisions.id'),nullable=False,index=True)
    item_id=Column(Integer,ForeignKey('item_master.id'),nullable=False,index=True)
    equipment_id=Column(Integer,ForeignKey('equipment_masters.id'),nullable=False,index=True)


class ProductionPlanRevisionApplication(Base):
    __tablename__='production_plan_revision_applications'
    id=Column(Integer,primary_key=True)
    month=Column(String(7),ForeignKey('production_plan_revision_states.month'),nullable=False,index=True)
    revision_id=Column(Integer,ForeignKey('production_plan_revisions.id'),nullable=False)
    reason=Column(Text,nullable=False)
    applied_by=Column(String(100),nullable=False)
    applied_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ProductionPlanRevisionEdit(Base):
    __tablename__='production_plan_revision_edits'
    id=Column(Integer,primary_key=True)
    revision_id=Column(Integer,ForeignKey('production_plan_revisions.id'),nullable=False,index=True)
    process_code=Column(String(100),nullable=False)
    before_json=Column(Text,nullable=False)
    after_json=Column(Text,nullable=False)
    reason=Column(Text,nullable=False)
    edited_by=Column(String(100),nullable=False)
    edited_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ProductionPlanRevisionMigration(Base):
    __tablename__='production_plan_revision_migrations'
    month=Column(String(7),ForeignKey('production_plan_revision_states.month'),primary_key=True)
    migrated_at=Column(DateTime,nullable=False,default=datetime.utcnow)


class ProductionPlanRevisionDeletion(Base):
    __tablename__='production_plan_revision_deletions'
    id=Column(Integer,primary_key=True)
    month=Column(String(7),nullable=False,index=True)
    number=Column(Integer,nullable=False)
    source_revision_id=Column(Integer,nullable=False)
    payload_json=Column(Text,nullable=False)
    reason=Column(Text,nullable=False)
    deleted_by=Column(String(100),nullable=False)
    deleted_at=Column(DateTime,nullable=False,default=datetime.utcnow)
