import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# 운영 컨테이너에서는 /data/documents로 지정합니다. DB에는 상대경로만 저장합니다.
DOCUMENT_STORAGE_ROOT = Path(os.getenv("DOCUMENT_STORAGE_ROOT", str(BASE_DIR / "data" / "documents"))).resolve()
DOCUMENT_MAX_FILE_BYTES = int(os.getenv("DOCUMENT_MAX_FILE_BYTES", str(50 * 1024 * 1024)))
DOCUMENT_MAX_FILES = int(os.getenv("DOCUMENT_MAX_FILES", "10"))
DOCUMENT_ALLOWED_EXTENSIONS = tuple(
    value.strip().lower().lstrip(".")
    for value in os.getenv("DOCUMENT_ALLOWED_EXTENSIONS", "pdf,png,jpg,jpeg,tif,tiff,bmp,dwg,dxf,step,stp,igs,iges").split(",")
    if value.strip()
)
if DOCUMENT_MAX_FILE_BYTES < 1 or DOCUMENT_MAX_FILES < 1:
    raise ValueError("문서 파일 크기/개수 제한은 1 이상이어야 합니다.")

# 기존 미사용 정책을 서버/화면에서 공유합니다. 운영 환경변수로 변경할 수 있습니다.
SESSION_ADMIN_IDLE_MINUTES = int(os.getenv("SESSION_ADMIN_IDLE_MINUTES", "20"))
SESSION_USER_IDLE_MINUTES = int(os.getenv("SESSION_USER_IDLE_MINUTES", "30"))
SESSION_COOKIE_MAX_AGE_SECONDS = 86400 * 7  # 기존 로그인 쿠키 보관 상한
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
if SESSION_ADMIN_IDLE_MINUTES < 1 or SESSION_USER_IDLE_MINUTES < 1:
    raise ValueError("로그인 미사용 제한은 1분 이상이어야 합니다.")

# Environment credentials remain supported as a fallback for server-managed accounts.
PRODUCTION_SYNC_USER = os.getenv("PRODUCTION_SYNC_USER", "")
PRODUCTION_SYNC_PASSWORD = os.getenv("PRODUCTION_SYNC_PASSWORD", "")

PRODUCTION_SYNC_KEY_PATH = Path(os.getenv("PRODUCTION_SYNC_KEY_PATH", str(DOCUMENT_STORAGE_ROOT.parent / "production-sync.key"))).resolve()
