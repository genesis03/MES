# 계정별 서버 권한 및 Oracle IP HTTPS

## 반영되는 보안 변경

- `core/endpoint_permissions.json`은 현재 등록된 화면/API별 HTTP 메서드·경로 템플릿·처리 함수와 권한을 고정한다. 새 경로/처리 함수는 명시적으로 등록하기 전까지 일반 계정이 사용할 수 없다.
- `core/endpoint_access.py`가 현재 계정의 저장된 메뉴 권한으로 접근을 검사한다. `X-MES-Menu-Path`와 Referer는 권한 근거가 아니다. 감사 기록에는 기존처럼 남는다.
- 기존 계정 관리의 메뉴별 NONE/READ/WRITE 설정을 사용한다. NONE은 차단, READ는 조회/출력, WRITE는 조회/변경이다. 조회용 POST 엑셀/인쇄는 명시적 READ 예외로 등록했다. 변경을 일으키는 요청은 WRITE가 필요하다.
- 권한이 비어 있거나 손상됐으면 기본 차단한다. 구형 `enabled` 그룹은 계정 관리 화면의 기존 변환 방식처럼 명시적 허용 그룹만 READ로 호환한다. 새 계정/신규 메뉴에는 권한을 자동 추가하지 않는다.
- 관리자는 기존 관리 기능을 유지한다. 사용자 이름이 `admin`이라는 이유만으로 관리자 권한을 주지 않으며 실제 역할을 검사한다.
- 공용 코드/저장위치 조회는 업무 권한이 있는 계정에만 허용한다. 전체 거래처/품목 상세는 해당 메뉴 권한을 요구한다. BOM은 전체 품목 마스터 대신 품번·품명 등 6개 필드만 제공하는 별도 선택 API를 사용한다.
- 가공/조립 공용 조회는 실제 필터로 구분한다. 필터 없는 전체 가동 조회는 두 구분 모두의 권한이 필요하다. 특정 가동 ID는 DB에 저장된 구분으로 검사하므로 쿼리나 메뉴 헤더를 위조할 수 없다. 시작 요청은 업무 처리기의 공정/구분 일치 검증을 유지한다.
- 문서별/검사기준서별 기존 세부 권한 검사는 계속 적용된다. 전역 규칙은 그 검사를 대체하지 않는다.

현재 READ는 해당 기능의 다운로드도 허용한다. 별도 EXPORT 권한, 계정별 요청량 제한, 다운로드 건수 감사/탐지는 아직 추가하지 않았다. 정상 조회 권한자가 반복 수집하는 것을 완전히 막는 변경은 아니다.

배포 전 관리자에서 일반 계정별 메뉴 설정을 확인한다. 특히 과거 아무 권한도 지정하지 않은 계정은 이제 업무 API가 차단된다. 반영 후 일반 계정으로 허용 메뉴 조회/쓰기와 차단 메뉴 직접 URL 호출을 확인한다.

Docker 빌드에는 `.dockerignore`를 적용해 `.env`, DB, 인증서/키, 테스트 데이터, Git 기록이 이미지에 포함되지 않도록 했다. 기존 이미지/과거 저장소 기록의 비밀정보 제거는 별도 검토 대상이다.

## 현재 HTTPS가 안 된 이유와 준비한 전환

기존 `docker-compose.oracle.yml`은 호스트 80 → 앱 8080만 매핑한다. 이 파일 자체에는 443 리스너, 인증서, TLS 프록시가 없다. OCI 외부 설정 상태는 코드만으로 알 수 없다.

Oracle VM도 자체 HTTPS를 제공할 수 있다. Oracle의 별도 인증서 상품은 필수가 아니다. Let's Encrypt는 공인 IPv4/IPv6 인증서를 지원한다. IP 인증서는 약 160시간짜리이며 자동 갱신이 필수다. 공식 문서의 Certbot 5.4 이상 webroot/IP 지원 방식을 사용한다.

별도 `docker-compose.oracle-https.yml`은 Nginx만 80/443을 공개하고 앱은 내부 8080으로 연결한다. HTTPS 배포에서만 Secure 쿠키를 강제한다. HTTP는 인증서 검증 경로 외에는 설정된 IP의 HTTPS로 전환한다. 일반 HTTP 배포 파일은 바꾸지 않으므로 로컬 실행도 유지된다.

### 서버에서 먼저 확인할 것

1. 129.225.172.135가 이 VM에 할당된 고정/예약 공인 IP인지 확인한다. IP가 바뀌면 새 인증서가 필요하다.
2. OCI의 해당 VNIC NSG 또는 서브넷 Security List에서 TCP 80/443 인바운드를 허용한다. 운영용 443은 업무에 필요한 접속 범위로 제한할 수 있지만, 80의 ACME 검증 경로는 외부 검증 서버에서 접근 가능해야 한다. 사용 중인 다른 NSG/보안목록 규칙과 함께 확인한다.
3. VM 자체 방화벽에서도 80/443을 허용한다. 기존 SSH 접속 규칙을 삭제하거나 방화벽 전체를 비활성화하지 않는다.
4. 서버 MES 폴더의 `.env`, Docker Compose, `python3`, `openssl`, `systemd`가 필요하다. 기존 인증서/프록시가 별도로 있다면 이 전환을 중복 적용하지 않는다.
5. 전환 전에 DB와 documents 및 암호화 키가 있는 `data/`를 백업한다. 온라인 SQLite 파일을 단순 복사하지 말고 기존 백업 절차나 SQLite backup 기능을 사용한다. `data/https/letsencrypt`도 이후 함께 백업한다.

### 최초 전환 명령

아래 명령은 **오라클 서버의 MES 저장소 폴더에서** 실행한다. 현재 작업 환경에서는 실행하거나 운영 인증서를 발급하지 않았다.

```bash
git pull origin main
sudo bash scripts/oracle_https.sh enable 129.225.172.135 genesislee03@gmail.com
```

이 명령은 기존 앱을 빌드한 뒤 잠시 HTTP 서비스를 전환하고, IP 인증서를 발급한 다음 HTTPS 프록시와 하루 두 번 갱신 타이머를 설정한다. 최초 발급 중에는 HTTP 로그인 대신 503을 제공한다. 실패하면 기존 HTTP 구성으로 복원을 시도한다. 운영 접속 전환이므로 작업 시간을 정해 실행한다. 파일/DB/권한 설정을 초기화하지 않는다.

```bash
curl -I https://129.225.172.135/login
sudo systemctl list-timers mes-https-renew.timer
sudo systemctl start mes-https-renew.service
sudo journalctl -u mes-https-renew.service -n 30 --no-pager
```

브라우저에서 `https://129.225.172.135`로 로그인하고 자물쇠/인증서 IP를 확인한다. Secure 쿠키는 HTTPS에서만 보내므로 이후 HTTP 주소로 직접 로그인하지 않는다.

### 전환 이후 업데이트

HTTPS 전환 후에는 이전 HTTP compose로 재시작하지 않는다.

```bash
git pull origin main
docker compose -f docker-compose.oracle-https.yml up -d --build mes proxy
```

갱신은 인증서가 갱신할 시기에 가까울 때만 실제 발급하며, 완료 후 Nginx 설정 검사와 reload를 수행한다. 프로세스 재시작으로 DB를 초기화하지 않는다. 인증서 만료 여부와 타이머 실패는 운영자가 모니터링해야 한다.

### 수동 복원

자동 복원도 실패했거나 별도로 HTTP 복원이 필요하면 다음 순서로 실행한다. HTTP로 돌아가는 동안 통신은 암호화되지 않는다.

```bash
sudo systemctl disable --now mes-https-renew.timer
docker compose -f docker-compose.oracle-https.yml stop proxy
docker compose -f docker-compose.oracle.yml up -d --build mes
```

### 검증의 범위

계정 권한은 실제 라우터·미들웨어와 격리 DB로 확인한다. HTTPS는 격리 Nginx에서 테스트 인증서의 IP/서명을 검증하고 HTTP 리다이렉트, ACME 경로, HTTPS 헤더 전달을 시험한다. 테스트는 검증 우회 옵션을 사용하지 않는다. 공인 IP의 실제 인증서 발급과 OCI 방화벽/운영 배포는 서버에서 수행해야 하며 로컬 검사 통과가 이를 대신하지 않는다.

공식 자료:
- https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/
- https://letsencrypt.org/2026/03/11/shorter-certs-certbot/

## 이번 확인 결과와 남은 범위

- 전체 테스트: 842개 통과, 기존 실패 1개. 기존 실패는 `test_external_sync.py::test_common_settings_preserve_existing_values_until_explicit_save`의 연동 대상 개수 기대값(5개)과 현재 6개 대상의 불일치이며, 보안 수정 전 main에서도 동일하게 재현했다. 기능 변경이나 테스트 수정으로 덮지 않았다.
- 추가 권한 테스트는 실제 라우터/미들웨어에서 직접 API 호출, 헤더 위조, 권한 누락, 다른 가공·조립 ID, POST 본문 전달 및 일반 계정의 최소 선택 필드를 확인한다.
- 전체 앱 브라우저 테스트에서 제한 계정 로그인, 허용 재고 조회, 거래처 조회·수정 차단, 헤더 위조 차단, BOM 최소 조회를 확인했다.
- 격리 Nginx의 실제 TLS 연결/IP 인증서 검증, HTTP 리다이렉트, ACME 검증 파일 경로, 전달된 HTTPS/Host를 확인했다. 공식 Certbot 5.9.0 이미지의 IP/shortlived 옵션을 확인했다. 운영 인증서는 아직 발급하지 않았다.
- Docker 이미지를 실제로 내보내 확인해 DB·키·Git·테스트 자료가 포함되지 않고 새 권한 목록은 포함됨을 검증했다.
- 이 구성은 브라우저와 **우리 MES** 사이의 HTTPS다. 이전 MES `14.63.172.132:8081`로 데이터를 조회하는 구간은 여전히 HTTP이며, 해당 서버의 HTTPS나 안전한 사설 연결은 별도 작업이다.
- 로그인 시도 제한, 별도 EXPORT 권한, 계정별 조회량 제한 및 대량 반출 탐지는 이번 변경에 포함하지 않았다. 운영 HTTPS는 OCI 규칙 확인 후 서버에서 전환해야 한다.
