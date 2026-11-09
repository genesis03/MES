#!/usr/bin/env bash
# Run on the actual Oracle host. No cloud credentials are read or requested.
set -euo pipefail
mes_repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$mes_repo_root"
mes_https_compose=(docker compose -f docker-compose.oracle-https.yml)
mes_http_compose=(docker compose -f docker-compose.oracle.yml)
mes_https_state="$mes_repo_root/data/https"

case "${1:-}" in
  enable)
    if [[ $# -ne 3 ]]; then
      echo '사용법: bash scripts/oracle_https.sh enable 공인IP 인증서알림이메일' >&2
      exit 2
    fi
    if [[ $(id -u) -ne 0 ]]; then echo 'sudo bash scripts/oracle_https.sh enable 공인IP 이메일 로 실행하세요.' >&2; exit 2; fi
    mes_public_ip="$2"
    mes_email="$3"
    python3 - "$mes_public_ip" <<'PY'
import ipaddress,sys
address=ipaddress.ip_address(sys.argv[1])
if not address.is_global:
    raise SystemExit('인증서를 발급할 공인 IP 주소가 필요합니다.')
PY
    mes_existing_certificate=0
    if [[ -e "$mes_https_state/letsencrypt/live/mes-ip/fullchain.pem" ]]; then
      openssl x509 -in "$mes_https_state/letsencrypt/live/mes-ip/fullchain.pem" -checkip "$mes_public_ip" -noout
      mes_existing_certificate=1
    fi
    if [[ ! -f .env ]]; then echo '.env 파일이 없습니다.' >&2; exit 2; fi
    mkdir -p "$mes_https_state/nginx" "$mes_https_state/challenges" "$mes_https_state/letsencrypt"
    chmod 700 "$mes_https_state/letsencrypt"
    install -d -m 755 "$mes_https_state/challenges" "$mes_https_state/challenges/.well-known" "$mes_https_state/challenges/.well-known/acme-challenge"
    "${mes_https_compose[@]}" pull proxy certbot
    "${mes_https_compose[@]}" build mes
    "${mes_https_compose[@]}" run --rm --no-deps certbot --version
    if [[ $mes_existing_certificate == 0 ]]; then
      cp deploy/https/bootstrap.conf "$mes_https_state/nginx/default.conf"
    fi
    mes_migrating=1
    rollback() {
      if [[ ${mes_migrating:-0} == 1 ]]; then
        echo 'HTTPS 전환 실패: 기존 HTTP 서비스를 복원합니다.' >&2
        "${mes_https_compose[@]}" stop proxy || true
        "${mes_http_compose[@]}" up -d mes || true
      fi
    }
    trap rollback EXIT
    "${mes_http_compose[@]}" stop mes
    "${mes_https_compose[@]}" up -d mes proxy
    "${mes_https_compose[@]}" exec -T mes python - <<'PYTHON_READY'
import http.client,time
for attempt in range(20):
    try:
        connection=http.client.HTTPConnection('127.0.0.1',8080,timeout=2)
        connection.request('GET','/login')
        response=connection.getresponse()
        healthy=response.status==200
        response.read()
        connection.close()
        if healthy:
            break
    except OSError:
        pass
    time.sleep(2)
else:
    raise SystemExit('MES 기동 확인에 실패했습니다.')
PYTHON_READY
    if [[ $mes_existing_certificate == 0 ]]; then
      "${mes_https_compose[@]}" run --rm --no-deps certbot certonly \
        --non-interactive --agree-tos --email "$mes_email" \
        --preferred-profile shortlived --webroot --webroot-path /var/www/certbot \
        --cert-name mes-ip --ip-address "$mes_public_ip"
    fi
    python3 - "$mes_public_ip" "$mes_https_state/nginx/default.conf" <<'PY'
import ipaddress,sys
from pathlib import Path
address=ipaddress.ip_address(sys.argv[1])
host=f'[{address}]' if address.version==6 else str(address)
text=Path('deploy/https/secure.conf.template').read_text().replace('MES_HTTPS_ADDRESS',host)
Path(sys.argv[2]).write_text(text)
PY
    "${mes_https_compose[@]}" exec -T proxy nginx -t
    "${mes_https_compose[@]}" exec -T proxy nginx -s reload
    bash scripts/oracle_https.sh install-timer
    mes_migrating=0
    trap - EXIT
    echo 'HTTPS 전환 및 하루 두 번 자동 갱신 등록 완료.'
    ;;
  renew)
    "${mes_https_compose[@]}" run --rm --no-deps certbot renew --cert-name mes-ip --quiet
    "${mes_https_compose[@]}" exec -T proxy nginx -t
    "${mes_https_compose[@]}" exec -T proxy nginx -s reload
    ;;
  install-timer)
    if [[ $(id -u) -ne 0 ]]; then echo 'sudo bash scripts/oracle_https.sh install-timer 로 실행하세요.' >&2; exit 2; fi
    if [[ ! -e "$mes_https_state/letsencrypt/live/mes-ip/fullchain.pem" ]]; then echo '먼저 enable을 완료하세요.' >&2; exit 2; fi
    python3 - "$mes_repo_root" <<'PY'
import sys
from pathlib import Path
root=Path(sys.argv[1])
def quoted(value):
    return '"'+str(value).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
Path('/etc/systemd/system/mes-https-renew.service').write_text(
    '[Unit]\nDescription=MES IP certificate renewal\nAfter=docker.service network-online.target\nWants=network-online.target\n\n'
    '[Service]\nType=oneshot\nWorkingDirectory='+quoted(root)+'\nExecStart=/bin/bash '+quoted(root/'scripts/oracle_https.sh')+' renew\n')
Path('/etc/systemd/system/mes-https-renew.timer').write_text(
    '[Unit]\nDescription=Renew MES HTTPS certificates twice daily\n\n[Timer]\n'
    'OnCalendar=*-*-* 00,12:00:00\nRandomizedDelaySec=300\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n')
PY
    systemctl daemon-reload
    systemctl enable --now mes-https-renew.timer
    systemctl list-timers mes-https-renew.timer --no-pager
    ;;
  *)
    echo '사용법: bash scripts/oracle_https.sh {enable 공인IP 이메일|renew|install-timer}' >&2
    exit 2
    ;;
esac
