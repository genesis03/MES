from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from core.config import BASE_DIR
from core.database import SessionLocal
import models  # 기존 테이블 자동 생성 트리거
import models.partner  # 신규 거래처 테이블 자동 생성 트리거
from models.models import UserModel
from core.security import (
    DEV_BYPASS_AUTH,
    check_admin_permission,
    init_default_accounts,
    parse_user_permissions,
    verify_session_token,
)
from services.purchase_lot_format import install_purchase_lot_format
from services.production_lot_service import ensure_production_output_lots
from services.subcontract_reservation_repair import repair_cancelled_subcontract_reservations
from services.subcontract_inbound_repair import repair_subcontract_inbound_sample_stock
from services.audit_log import (
    install_audit_logging,
    reset_audit_context,
    set_audit_context,
)

from routers import pages, manual, shipping, basic_info, basic_info_workers, basic_info_equipment, bom, partner, admin, admin_process_code, auth, purchase, purchase_pages, purchase_inquiry, purchase_delete_guard, purchase_edit, subcontract, subcontract_pages, subcontract_inquiry, subcontract_outbound, subcontract_inbound, subcontract_inbound_lot_policy, subcontract_inbound_edit, purchase_unreceived, quality_pages, quality, quality_standard, quality_production_defects, quality_defect_status, production_pages, production, production_complete, production_run, production_run_delete, production_run_lot_fix, production_extra, inventory_lot_location, inventory, inventory_lot_trace, inventory_lot_trace_tree, inventory_lot_usage_trace, internal_labels, packing, sales, sales_order_policy, sales_shipping_direct, sales_shipping_direct_page, sales_shipping_fifo_auto, sales_shipping_partial_confirm, sales_shipping_entry, sales_order_delete, shipping_inquiry
from routers import control_plan
from routers import documents, inspection_standards, standard_documents, process_fmea, process_flow

# 초기 계정 데이터 생성 트리거
init_default_accounts()
# 구매입고 내부 LOT은 LR+YYMMDD+3자리(001~999) 순번 규칙으로 발번합니다.
install_purchase_lot_format()
# 기존 생산실적까지 포함해 생산 LOT가 빠진 건을 보강합니다.
ensure_production_output_lots()
# 과거 출고취소 건에 남은 외주 LOT 예약을 해제해 가용재고를 복원합니다.
repair_cancelled_subcontract_reservations()
# 외주입고 LOT는 최초수량을 유지하고 샘플수량은 사용수량으로 계산하도록 과거 데이터를 보정합니다.
repair_subcontract_inbound_sample_stock()

# 사용자 업무행위 감사로그는 초기 보정 작업 이후부터 기록합니다.
install_audit_logging()

app = FastAPI(title="출하 바코드 관리 시스템")


def _menu_level(value) -> str:
    if value is True:
        return "READ"
    if value is False or value is None:
        return "NONE"
    level = str(value).strip().upper()
    return level if level in {"NONE", "READ", "WRITE"} else "NONE"


@app.middleware("http")
async def audit_request_context(request: Request, call_next):
    """업무 데이터 변경 시 사용자/메뉴/요청 정보를 감사로그에 연결합니다."""
    request_path = request.url.path
    token = request.cookies.get("session_token")
    username = getattr(request.state, "authenticated_username", None)
    if not username and token:
        username = verify_session_token(token)
    user_id = None

    if username:
        db = SessionLocal()
        try:
            user = db.query(UserModel).filter(UserModel.username == username).first()
            if user:
                user_id = user.id
                username = user.username
        finally:
            db.close()

    if not username:
        username = "DEV_BYPASS" if DEV_BYPASS_AUTH else "ANONYMOUS"

    menu_path = (request.headers.get("X-MES-Menu-Path") or "").strip()
    if not menu_path:
        referer = request.headers.get("referer") or ""
        if referer:
            menu_path = urlparse(referer).path
    if not menu_path and not request_path.startswith("/api/"):
        menu_path = request_path

    client_ip = request.client.host if request.client else None
    audit_token = set_audit_context(
        user_id=user_id,
        username=username,
        menu_path=menu_path or None,
        request_path=request_path,
        request_method=request.method.upper(),
        ip_address=client_ip,
    )
    try:
        return await call_next(request)
    finally:
        reset_audit_context(audit_token)


@app.middleware("http")
async def enforce_menu_write_permission(request: Request, call_next):
    """일반 계정의 메뉴 접근 및 READ/WRITE 권한을 서버에서도 검사합니다."""
    if DEV_BYPASS_AUTH:
        return await call_next(request)

    req_path = request.url.path.rstrip("/") or "/"
    if req_path.startswith("/static/") or req_path in {"/login", "/logout", "/api/login", "/api/logout", "/api/session/activity"}:
        return await call_next(request)

    token = request.cookies.get("session_token")
    username = getattr(request.state, "authenticated_username", None)
    if not username and token:
        username = verify_session_token(token)
    if not username:
        return await call_next(request)

    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == username).first()
        if not user or check_admin_permission(user):
            return await call_next(request)

        perms = parse_user_permissions(user)
        menu_access = perms.get("menu_access")
        if not isinstance(menu_access, dict) or not menu_access:
            return await call_next(request)

        normalized = {
            (str(path).rstrip("/") or "/"): _menu_level(level)
            for path, level in menu_access.items()
            if str(path or "").strip()
        }

        def match_menu(path_value: str):
            path_value = (path_value or "").rstrip("/") or "/"
            return next(
                (
                    path for path in sorted(normalized, key=len, reverse=True)
                    if path_value == path or (path != "/" and path_value.startswith(path + "/"))
                ),
                None,
            )

        method = request.method.upper()

        # 화면 URL 직접 접근: NONE이면 차단
        if method in {"GET", "HEAD"} and not req_path.startswith("/api/"):
            matched = match_menu(req_path)
            if matched and normalized[matched] == "NONE":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "해당 메뉴에 대한 접근 권한이 없습니다."},
                )

        # 쓰기 요청: 호출한 화면이 WRITE가 아니면 차단
        if method in {"POST", "PUT", "PATCH", "DELETE"}:
            source_path = (request.headers.get("X-MES-Menu-Path") or "").strip()
            if not source_path:
                referer = request.headers.get("referer") or ""
                if referer:
                    source_path = urlparse(referer).path
            matched = match_menu(source_path)
            if matched and normalized[matched] != "WRITE":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "읽기 전용 권한입니다. 저장/수정/삭제/확정 작업을 할 수 없습니다."},
                )
    finally:
        db.close()

    return await call_next(request)


@app.middleware("http")
async def enforce_login_session(request: Request, call_next):
    """창을 닫아도 서버 만료시각이 지나면 모든 업무 화면/API의 인증을 차단합니다."""
    path = request.url.path.rstrip("/") or "/"
    if DEV_BYPASS_AUTH or path.startswith("/static/") or path in {
        "/login", "/logout", "/api/login", "/api/logout"
    }:
        return await call_next(request)
    token = request.cookies.get("session_token")
    username = verify_session_token(token) if token else None
    if username:
        request.state.authenticated_username = username
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "private, no-store")
        return response
    if path.startswith("/api/"):
        response = JSONResponse(status_code=401,
            content={"detail": "로그인 세션이 만료되었습니다. 다시 로그인해 주세요."},
            headers={"Cache-Control": "no-store"})
    else:
        response = RedirectResponse(url="/login?reason=idle", status_code=303)
    response.delete_cookie("session_token")
    return response


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
app.include_router(documents.router)
# 새 검사기준서 공통 화면/API를 기존 안내 라우터보다 먼저 등록합니다.
app.include_router(inspection_standards.router)
app.include_router(standard_documents.router)
app.include_router(process_fmea.router)
app.include_router(control_plan.router)
app.include_router(process_flow.router)
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
# 은도금=LZ / 외주 CNC=LC LOT 정책을 기존 외주입고 생성 경로보다 먼저 적용합니다.
app.include_router(subcontract_inbound_lot_policy.router)
app.include_router(subcontract_inbound.router)
app.include_router(subcontract_inbound_edit.router)
app.include_router(quality_pages.router)
app.include_router(quality.router)
app.include_router(quality_standard.router)
app.include_router(quality_production_defects.router)
app.include_router(quality_defect_status.router)
app.include_router(production_pages.router)
app.include_router(production.router)
# 동일 complete 경로 중 생산 LOT 생성 버전을 먼저 등록합니다.
app.include_router(production_complete.router)
app.include_router(production_run_delete.router)
# 외주가공 입고 완료 LOT는 과거 외주 LOT 예약을 다시 차감하지 않도록 우선 적용합니다.
app.include_router(production_run_lot_fix.router)
app.include_router(production_run.router)
app.include_router(production_extra.router)
# LOT 재고 현황은 외주 출고/입고 이력까지 반영한 현재 저장위치 API를 우선 사용합니다.
app.include_router(inventory_lot_location.router)
app.include_router(inventory.router)
app.include_router(inventory_lot_trace.router)
app.include_router(inventory_lot_usage_trace.router)
app.include_router(inventory_lot_trace_tree.router)
# 내부 LOT 라벨은 기존 출고 라벨 시스템과 별도 경로에서 출력합니다.
app.include_router(internal_labels.router)
app.include_router(packing.router)
# 수주 목적(양산/샘플/개발)과 거래구분(유상/무상) API를 기존 sales보다 먼저 적용합니다.
app.include_router(sales_order_policy.router)
# 샘플/개발은 포장 없이 미포장 생산 LOT에서 직접 출고할 수 있습니다.
app.include_router(sales_shipping_direct.router)
# 출고 입력 화면은 직출고 확장 스크립트를 포함한 템플릿을 우선 사용합니다.
app.include_router(sales_shipping_direct_page.router)
# 양산은 뒤 LOT를 스캔해도 선입 LOT부터 지정 출고수량까지 자동 배정합니다.
app.include_router(sales_shipping_fifo_auto.router)
# 다중 수주 출고의 금회 출고 지정수량 검증/확정 API를 기존 출고 확정 경로보다 먼저 적용합니다.
app.include_router(sales_shipping_partial_confirm.router)
# 새 출고 입력 화면/스캔 API는 기존 sales 동일 경로보다 먼저 등록합니다.
app.include_router(sales_shipping_entry.router)
app.include_router(sales.router)
app.include_router(sales_order_delete.router)
app.include_router(shipping_inquiry.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
