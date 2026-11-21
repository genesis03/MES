#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

mes_host="${1:-129.225.172.135}"
if [[ $# -gt 1 || ! "$mes_host" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]; then
    echo '사용법: bash scripts/oracle_update.sh [HTTPS 주소의 호스트명 또는 IPv4]' >&2
    exit 2
fi
if [[ "$(git branch --show-current)" != main ]]; then
    echo 'main 브랜치에서 실행하세요.' >&2
    exit 2
fi
exec 9>"$(git rev-parse --git-path mes-oracle-update.lock)"
if ! flock -n 9; then
    echo '이미 오라클 업데이트가 진행 중입니다.' >&2
    exit 1
fi

compose=(docker compose -f docker-compose.oracle-https.yml)
if (( EUID != 0 )); then
    sudo -v
    compose=(sudo "${compose[@]}")
fi

git pull --ff-only origin main
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse FETCH_HEAD)" ]]; then
    echo '로컬 main과 가져온 원격 main이 다릅니다. 배포를 중단합니다.' >&2
    exit 1
fi

# Validate the HTTPS configuration before replacing the running app.
"${compose[@]}" run --rm --no-deps proxy nginx -t
if ! "${compose[@]}" up -d --build --wait --wait-timeout 120 mes; then
    echo 'MES가 제한 시간 안에 준비되지 않았습니다.' >&2
    "${compose[@]}" ps -a >&2
    exit 1
fi

# Nginx resolves the upstream address at startup. Recreate it after MES is ready.
"${compose[@]}" up -d --no-deps --force-recreate proxy
for ((attempt=1; attempt<=12; attempt++)); do
    if status="$(curl --noproxy '*' --connect-to "${mes_host}:443:127.0.0.1:443" \
        --connect-timeout 3 --max-time 5 --silent --show-error --output /dev/null \
        --write-out '%{http_code}' "https://${mes_host}/login")" && [[ "$status" == 200 ]]; then
        printf '배포 완료 %s · HTTPS /login 응답 %s\n' "$(git rev-parse --short HEAD)" "$status"
        exit 0
    fi
    sleep 2
done

echo 'HTTPS 로그인 응답 확인에 실패했습니다.' >&2
"${compose[@]}" ps -a >&2
exit 1
