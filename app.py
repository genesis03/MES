from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.config import BASE_DIR
import models  # 기존 테이블 자동 생성 트리거
import models.partner  # 신규 거래처 테이블 자동 생성 트리거
from core.security import init_default_accounts

from routers import pages, manual, shipping, basic_info, basic_info_workers, basic_info_equipment, bom, partner, admin, admin_process_code, auth, purchase, purchase_pages, purchase_inquiry, purchase_edit, subcontract, subcontract_pages, subcontract_inquiry, subcontract_outbound, subcontract_inbound, purchase_unreceived, quality_pages, quality, production_pages, production, production_run, production_extra

# 초기 계정 데이터 생성 트리거
init_default_accounts()

app = FastAPI(title="출하 바코드 관리 시스템")

# 정적 파일 경로 마운트
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 모듈별 라우터 등록
app.include_router(auth.router)
# 최고관리자 공정코드 변경 화면은 기존 admin 동일 경로보다 먼저 등록합니다.
app.include_router(admin_process_code.router)
app.include_router(admin.router)
app.include_router(pages.router)
app.include_router(manual.router)
app.include_router(shipping.router)
app.include_router(basic_info.router)
app.include_router(basic_info_workers.router)
app.include_router(basic_info_equipment.router)
app.include_router(bom.router)
app.include_router(admin.public_api_router)
app.include_router(partner.router)
app.include_router(purchase.router)
app.include_router(purchase.api_router)
app.include_router(subcontract_pages.router)
app.include_router(purchase_unreceived.page_router)
app.include_router(purchase_pages.router)
app.include_router(purchase_inquiry.router)
app.include_router(purchase_edit.router)
app.include_router(purchase_unreceived.router)
app.include_router(subcontract.router)
app.include_router(subcontract_inquiry.router)
app.include_router(subcontract_outbound.router)
app.include_router(subcontract_inbound.router)
app.include_router(quality_pages.router)
app.include_router(quality.router)
app.include_router(production_pages.router)
app.include_router(production.router)
app.include_router(production_run.router)
app.include_router(production_extra.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
