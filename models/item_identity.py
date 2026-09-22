from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from core.database import Base


class ItemPartNoHistory(Base):
    """품번 변경 이력.

    item_master.id는 영구 식별자로 유지하고, 사용자가 보는 품번(part_no)이
    변경될 때 이전/신규 품번과 변경 사유를 기록합니다.
    """

    __tablename__ = "item_part_no_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(Integer, ForeignKey("item_master.id"), nullable=False, index=True)
    old_part_no = Column(String(100), nullable=False, index=True)
    new_part_no = Column(String(100), nullable=False, index=True)
    changed_by = Column(String(100), nullable=True)
    reason = Column(Text, nullable=True)
    changed_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
