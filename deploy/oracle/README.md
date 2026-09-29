# Oracle Cloud 배포 준비

## 1. 서버 디렉터리
권장 경로: `/opt/mes`

```bash
sudo mkdir -p /opt/mes/data
sudo chown -R $USER:$USER /opt/mes
cd /opt/mes
git clone https://github.com/genesis03/MES.git .
```

## 2. 기존 운영 DB 유지
현재 GitHub의 `manual_labels.db` 또는 실제 운영 PC의 최신 DB를 서버의 영구 볼륨으로 복사합니다.

```bash
cp manual_labels.db data/manual_labels.db
```

운영 PC의 DB가 GitHub 파일보다 최신이면 반드시 운영 PC의 파일을 사용합니다. 기존 LOT/이력 데이터는 초기화하지 않습니다.

## 3. 환경변수
```bash
cp .env.example .env
nano .env
```

반드시 변경:
- `SECRET_KEY`
- `DEFAULT_ADMIN_PASSWORD`
- `DEFAULT_USER_PASSWORD`

기존 DB에 이미 계정이 있으면 초기 비밀번호 환경변수는 기존 계정 비밀번호를 바꾸지 않습니다.

## 4. 실행
Docker Engine과 Docker Compose plugin 설치 후:

```bash
docker compose -f docker-compose.oracle.yml up -d --build
docker compose -f docker-compose.oracle.yml ps
docker compose -f docker-compose.oracle.yml logs --tail=100 mes
```

## 5. 기본 접속
기본값은 서버의 `8080` 포트입니다.

Oracle Cloud 보안 목록/NSG와 Ubuntu UFW를 함께 확인해야 합니다. 인터넷에 직접 노출하기 전에는 관리자 계정 로그인과 일반계정 메뉴 권한을 먼저 검증합니다.

## 6. 업데이트
```bash
cd /opt/mes
git pull origin main
docker compose -f docker-compose.oracle.yml up -d --build
```

DB는 `./data/manual_labels.db`에 유지되므로 이미지 재빌드와 분리됩니다.

## 7. 배포 전 필수 확인
- `core/security.py`의 `DEV_BYPASS_AUTH = False`
- 관리자 로그인
- 일반계정 READ/WRITE 메뉴 권한
- 입고/생산/외주/포장/출고 주요 LOT 흐름
- 입고불량 LOT 수량 / 처리 가능 수량
- `data/manual_labels.db` 백업
- `.env`가 Git에 포함되지 않는지 확인
- 정적파일 및 업로드 기능 경로 확인
- 서버 재부팅 후 컨테이너 자동 재시작 확인

## 8. 백업 예시
```bash
mkdir -p backup
cp data/manual_labels.db "backup/manual_labels_$(date +%Y%m%d_%H%M%S).db"
```

SQLite 운영 중에는 가능하면 애플리케이션 정지 후 백업하거나 SQLite backup API를 사용하는 것이 안전합니다.
