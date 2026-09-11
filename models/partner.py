from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index
)
from sqlalchemy.orm import relationship

# core/database.py의 Base 객체 참조
from core.database import Base


class Partner(Base):
    """
    거래처 마스터 모델 (이카운트 기준 간소화 버전)
    """
    __tablename__ = "partners"

    id = Column(Integer, primary_key=True, autoincrement=True)
    partner_code = Column(String(30), unique=True, nullable=False, index=True)
    partner_name = Column(String(100), nullable=False, index=True)  # 상호
    partner_type = Column(String(20), nullable=False, default="VENDOR")  # VENDOR(구매처), CUSTOMER(고객사), BOTH(겸용)
    
    ceo_name = Column(String(50), nullable=True)
    tel = Column(String(30), nullable=True)
    mobile = Column(String(30), nullable=True)
    fax = Column(String(30), nullable=True)
    tax_email = Column(String(100), nullable=True)  # 계산서 이메일
    
    post_code = Column(String(10), nullable=True)
    address_base = Column(String(200), nullable=True)
    address_detail = Column(String(200), nullable=True)
    
    manager_name = Column(String(50), nullable=True)  # 사내 담당자
    is_active = Column(String(1), nullable=False, default="Y")  # 'Y' 또는 'N'
    remark = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    # 1:N 거래처 담당자 관계 (거래처 삭제 시 종속 삭제)
    contacts = relationship(
        "PartnerContact",
        back_populates="partner",
        cascade="all, delete-orphan",
        lazy="selectin"
    )


class PartnerContact(Base):
    """
    거래처 담당자 모델 (1:N 하위 탭 관리)
    """
    __tablename__ = "partner_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    partner_id = Column(
        Integer,
        ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    contact_name = Column(String(50), nullable=False)  # 담당자명
    department = Column(String(50), nullable=True)     # 담당자 부서
    position = Column(String(50), nullable=True)       # 담당자 직책
    handled_item = Column(String(100), nullable=True)  # 아이템(취급품목)
    extension_tel = Column(String(30), nullable=True)  # 내선번호
    mobile_phone = Column(String(30), nullable=True)   # 핸드폰번호
    email = Column(String(100), nullable=True)         # 개인 이메일
    
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    # N:1 역참조
    partner = relationship("Partner", back_populates="contacts")


# 검색 인덱스 구성
Index("ix_partners_type_active", Partner.partner_type, Partner.is_active)