# 메뉴 연결 및 준비 중 안내 점검

점검일: 2026-10-07

## 범위와 결과

애플리케이션을 실행하거나 DB를 변경하지 않고, 사이드바 링크와 실제 라우터 등록 순서, 화면 파일, 준비 중 안내를 소스로 확인했다. 사용자 요청에 따라 기능 테스트와 기존 테스트는 실행하지 않았다.

- 사이드바 하위 메뉴 54개의 GET 경로가 모두 등록되어 있다.
- 등록된 라우트가 직접 지정한 화면 파일의 누락은 발견하지 못했다.
- 먼저 등록한 동적 경로가 뒤의 고정 경로를 가리는 사례는 점검한 경로에서 발견하지 못했다.
- 변경 전 중복된 메서드·경로 조합은 19개였다. 실제 요청을 처리하던 최초 등록을 유지하고 뒤의 중복 등록 20개를 제거해 중복 조합이 0개가 되었다.

## 중복 경로별 처리

| 메서드·경로 | 유지한 구현 | 이유 |
| --- | --- | --- |
| GET /admin/processes-locations | admin_process_code | 최고관리자 공정코드 변경 화면 |
| GET /api/common-codes/lookup | admin.router | 같은 함수의 이중 등록 제거 |
| GET /api/locations/lookup | admin.router | 같은 함수의 이중 등록 제거 |
| GET /standard-documents/inspection-standards | inspection_standards | 검사 유형별 권한과 외관검사 탭 진입 |
| GET /subcontract/orders | subcontract_pages | 실제 외주 발주 화면 유지 |
| GET /subcontract/outbound | subcontract_pages | 실제 외주 출고 화면 유지 |
| GET /subcontract/inbound | subcontract_pages | 실제 외주 입고 화면 유지 |
| GET /purchase/unreceived | purchase_unreceived.page_router | 실제 미입고 화면 유지 |
| GET /api/purchase/inquiry/orders | purchase_pages | 현재 구매 조회 응답과 날짜 검증 유지 |
| GET /api/purchase/inquiry/inbounds | purchase_pages | 현재 구매 조회 필터와 응답 유지 |
| POST /api/purchase/inquiry/inbounds/delete-selected | purchase_delete_guard | 생산에 사용된 LOT 삭제 제한 유지 |
| POST /api/subcontract/inbound | subcontract_inbound_lot_policy | LZ/LC LOT 생성 정책 유지 |
| GET /production/packing | production_pages | 같은 포장 화면의 이중 등록 제거 |
| POST /api/production-run/{run_id}/complete | production_complete | 생산 완료 후 생산 LOT 생성 유지 |
| POST /api/production-run/{run_id}/scan-lot | production_run_lot_fix | 외주에서 돌아온 LOT의 과거 예약 중복 차감 방지 유지 |
| GET /api/inventory/lots | inventory_lot_location | 외주 이력과 현재 보관위치를 반영한 조회 유지 |
| GET /sales/shipping | sales_shipping_direct_page | 직출고 기능 포함 화면 유지; 뒤의 중복 2개 제거 |
| POST /api/sales/shipping-entry/scan | sales_shipping_fifo_auto | 지정 수량별 FIFO 배정 유지 |
| POST /api/sales/shipping-entry/confirm | sales_shipping_partial_confirm | 금회 지정 수량과 부분 출고 검증 유지 |

함수 본문은 삭제하지 않았다. 특히 `production_run.complete_run`은 생산 완료 라우터에서 직접 호출하므로 유지해야 한다. 중복 HTTP 등록만 제거하면 실제 호출 동작을 유지하면서 자동 API 문서에 뒤의 구형 입력 스키마가 표시되는 문제도 방지할 수 있다.

변경 전 HEAD와 변경 후 소스를 AST로 비교했다. 고유 메서드·경로 356개의 실제 선택 함수와 함수 정의가 모두 동일하고, 사이드바 54개 연결도 유지된다. 라우터·앱 Python 문법, 공통 안내 Jinja 문법과 diff 공백 오류를 확인했다. 공통 안내 화면 변경은 라우트 함수가 호출하는 안내 헬퍼에 적용했다.

## 준비 중 기능

| 기능 | 안내 |
| --- | --- |
| 작업표준서 | 공통 개발 예정 화면 |
| 포장사양서 | 공통 개발 예정 화면 |
| 외관검사 기준서 | 검사기준서 내부 탭의 공통 개발 예정 화면 |
| 공정순회 검사 | 공통 개발 예정 화면 |
| 입고 성적서 | 공통 개발 예정 화면 |
| 출하 성적서 | 공통 개발 예정 화면 |
| 수정품 가공 처리(리워크) | 이번 변경으로 공통 개발 예정 화면에 통일 |
| 설비 가동 현황(OEE) | 이번 변경으로 공통 개발 예정 화면에 통일 |

리워크와 설비 가동 현황의 설명은 이미 기능이 제공되는 것으로 읽힐 수 있어 '준비 중입니다'로 변경했다. 사용하지 않게 된 생산관리 전용 안내 템플릿은 제거했다. 실제 포장 처리 화면은 유지했다.

## 남은 검토

- 구매 조회의 비활성 구형 구현에는 offset/limit 페이지 처리와 입고 상태 필터·공란 상태 정규화가 있고, 현재 실제 호출되는 구현은 날짜 범위 검증과 최대 2,000행 조회를 사용한다. 이번에는 현재 호출 동작을 유지했다. 페이지 처리나 입고 상태 검색을 통합할지는 별도 변경에서 결정해야 한다.
- HTTP 등록이 제거된 구형 함수의 본문을 추가로 정리하려면 내부 호출·가져오기 관계를 별도로 점검해야 한다.
- 과거 테스트 결과인 16개 통과, 12개 실패, 15개 오류는 이번 점검으로 갱신되지 않았다. 테스트용 DB와 세션 발급의 불일치, 구매 테스트의 필수 보관위치 누락 등은 별도 수정 대상이다.
- 정적 연결 점검 결과는 실제 로그인, 조회, 저장, 업로드와 인쇄의 정상 작동을 보증하지 않는다. 내부 PC에서 사용자가 확인한다.

## 적용

Python 라우터 변경이 포함되어 있어 내부 PC에서 최신 코드 적용 후 MES 애플리케이션을 재시작하고 브라우저를 새로고침한다. DB와 업로드 파일을 변경할 필요는 없다.
