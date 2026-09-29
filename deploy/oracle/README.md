# Oracle Cloud 운영 배포

현재 운영 경로는 `/home/ubuntu/MES`를 기준으로 합니다.

## 1. 배포 구조

- 코드 원본: GitHub `genesis03/MES`
- 로컬 검증: 사용자 PC
- 운영 서버: Oracle Cloud `/home/ubuntu/MES`
- 운영 DB: `/home/ubuntu/MES/data/manual_labels.db`
- 외부 접속: 80 포트
- 컨테이너 내부 앱: 8080 포트

운영 DB는 GitHub DB와 분리해서 관리합니다. Oracle 운영이 시작된 뒤에는 GitHub의 `manual_labels.db`로 운영 DB를 덮어쓰지 않습니다.

## 2. 최초 배포

```bash
cd /home/ubuntu
git clone https://github.com/genesis03/MES.git MES
cd MES
mkdir -p data
cp manual_labels.db data/manual_labels.db
cp .env.example .env
docker compose -f docker-compose.oracle.yml up -d --build
```

`.env`의 `SECRET_KEY`, `DEFAULT_ADMIN_PASSWORD`, `DEFAULT_USER_PASSWORD`는 실제 운영값으로 변경합니다.

## 3. 정상 업데이트 절차

로컬 PC에서 먼저 GitHub 최신본을 받아 기능을 검증한 뒤 이상이 없을 때만 Oracle에 적용합니다.

Oracle 서버:

```bash
cd /home/ubuntu/MES
git pull origin main
docker compose -f docker-compose.oracle.yml up -d --build
docker compose -f docker-compose.oracle.yml ps
docker compose -f docker-compose.oracle.yml logs --tail=100 mes
```

일반 코드 업데이트에서는 `data/manual_labels.db`를 복사하거나 덮어쓰지 않습니다.

## 4. 접속

외부 80 포트를 컨테이너 8080 포트로 연결합니다.

```text
http://서버공인IP
```

Compose 설정:

```yaml
ports:
  - "80:8080"
```

## 5. 운영 DB 수동 백업

실행 중인 SQLite DB를 단순 `cp`하지 않고 SQLite backup API를 사용합니다.

```bash
cd /home/ubuntu/MES
docker compose -f docker-compose.oracle.yml exec -T mes \
  python deploy/oracle/backup_db.py \
  --source /data/manual_labels.db \
  --backup-dir /data/backup \
  --retention-days 14
```

백업 파일은 호스트 기준:

```text
/home/ubuntu/MES/data/backup/
```

에 저장됩니다.

## 6. 매일 자동 백업

기본 정책은 매일 03:00, 14일 보관입니다.

```bash
cd /home/ubuntu/MES
sudo cp deploy/oracle/mes-backup.service /etc/systemd/system/
sudo cp deploy/oracle/mes-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mes-backup.timer
```

타이머 확인:

```bash
systemctl list-timers --all | grep mes-backup
```

즉시 1회 테스트:

```bash
sudo systemctl start mes-backup.service
sudo systemctl status mes-backup.service --no-pager
ls -lh /home/ubuntu/MES/data/backup/
```

## 7. 운영 확인 항목

- 관리자 로그인 정상
- 일반계정 메뉴 READ/WRITE 권한 정상
- 구매/외주/생산/재고/품질/포장/출고 주요 화면 정상
- 입고불량에서 현재 재고 0 LOT 제외
- `data/manual_labels.db` 유지
- `.env` Git 미추적
- 서버 재부팅 후 MES 컨테이너 자동 재시작
- `mes-backup.timer` 활성 상태
- 백업 DB `PRAGMA integrity_check` 통과

## 8. 무료 운영 원칙

Oracle 무료 인스턴스 안에서 단일 MES 컨테이너 + SQLite 구조를 유지합니다. 별도 유료 DB, 로드밸런서, 추가 스토리지 서비스는 필요성이 확인되기 전까지 사용하지 않습니다.
