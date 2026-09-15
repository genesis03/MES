from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.config import BASE_DIR
import models  # 기존 테이블 자동 생성 트리거
import models.partner  # 신규 거래처 테이블 자동 생성 트리거
from core.security import init_default_accounts
from services.purchase_lot_format import install_purchase_lot_format
from services.production_lot_service import ensure_production_output_lots

from routers import pages, manual, shipping, basic_info, basic_info_workers, basic_info_equipment, bom, partner, admin, admin_process_code, auth, purchase, purchase_pages, purchase_inquiry, purchase_delete_guard, purchase_edit, subcontract, subcontract_pages, subcontract_inquiry, subcontract_outbound, subcontract_inbound, subcontract_inbound_edit, purchase_unreceived, quality_pages, quality, production_pages, production, production_complete, production_run, production_run_delete, production_run_lot_fix, production_extra, inventory, packing, sales

# 초기 계정 데이터 생성 트리거
init_default_accounts()
# 구매입고 내부 LOT은 LR+YYMMDD+99+1자리 순번 규칙으로 발번합니다.
install_purchase_lot_format()
# 기존 생산실적까지 포함해 생산 LOT가 빠진 건을 보강합니다.
ensure_production_output_lots()

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
# 기존 입고 삭제 경로보다 먼저 등록해 사용된 LOT 삭제를 차단합니다.
app.include_router(purchase_delete_guard.router)
app.include_router(purchase_inquiry.router)
app.include_router(purchase_edit.router)
app.include_router(purchase_unreceived.router)
app.include_router(subcontract.router)
app.include_router(subcontract_inquiry.router)
app.include_router(subcontract_outbound.router)
app.include_router(subcontract_inbound.router)
app.include_router(subcontract_inbound_edit.router)
app.include_router(quality_pages.router)
app.include_router(quality.router)
app.include_router(production_pages.router)
app.include_router(production.router)
# 동일 complete 경로 중 생산 LOT 생성 버전을 먼저 등록합니다.
app.include_router(production_complete.router)
app.include_router(production_run_delete.router)
# 외주가공 입고 완료 LOT는 과거 외주 LOT 예약을 다시 차감하지 않도록 우선 적용합니다.
app.include_router(production_run_lot_fix.router)
app.include_router(production_run.router)
app.include_router(production_extra.router)
app.include_router(inventory.router)
app.include_router(packing.router)
app.include_router(sales.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
