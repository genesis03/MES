from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from core.config import BASE_DIR, DATABASE_URL

if DATABASE_URL:
    engine = create_engine(DATABASE_URL)
else:
    DB_PATH = BASE_DIR / "manual_labels.db"
    engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})

Base = declarative_base()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    """데이터베이스 세션 의존성 제너레이터 (자동 자원 해제)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
