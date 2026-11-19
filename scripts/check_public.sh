#!/usr/bin/env bash
# 프록시(Caddy)를 거친 공개 경로 확인 (#7, PR #40 리뷰). 서버(호스트)에서 실행한다.
#   scripts/check_public.sh
#
# - IP 모드(SITE_ADDRESS=:80): http://127.0.0.1:<PROXY_HTTP_PORT>/healthz/ 를 Host 헤더(ALLOWED_HOSTS 첫 값)와 함께 호출
# - 도메인 모드: curl --resolve로 https://<도메인>/healthz/ 를 이 서버로 보내 **인증서까지 검증**
#   (인증서 첫 발급에 시간이 걸리므로 재시도: PUBLIC_CHECK_RETRIES(기본 12) × PUBLIC_CHECK_DELAY(기본 10초))
# - 응답 JSON의 status와 message_classifier.status가 모두 "ok"여야 통과
# 외부 인터넷에서 본 DNS·방화벽은 이 서버 안에서는 확인할 수 없으므로 배포 후 PC 브라우저로 따로 확인한다 (DEPLOY.md §4).
set -uo pipefail
cd "$(dirname "$0")/.."

get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//'; }
SITE="$(get SITE_ADDRESS)"; SITE="${SITE:-:80}"
HTTP_PORT="${PUBLIC_CHECK_HTTP_PORT:-$(get PROXY_HTTP_PORT)}"; HTTP_PORT="${HTTP_PORT:-80}"
HTTPS_PORT="${PUBLIC_CHECK_HTTPS_PORT:-$(get PROXY_HTTPS_PORT)}"; HTTPS_PORT="${HTTPS_PORT:-443}"
HOSTS="$(get DJANGO_ALLOWED_HOSTS)"; FIRST_HOST="${HOSTS%%,*}"
RETRIES="${PUBLIC_CHECK_RETRIES:-12}"
DELAY="${PUBLIC_CHECK_DELAY:-10}"

if [[ "$SITE" == :* ]]; then
    URL="http://127.0.0.1:${HTTP_PORT}/healthz/"
    CURL=(curl -sS --max-time 5 -H "Host: ${FIRST_HOST}" "$URL")
else
    URL="https://${SITE}:${HTTPS_PORT}/healthz/"
    CURL=(curl -sS --max-time 10 --resolve "${SITE}:${HTTPS_PORT}:127.0.0.1" "$URL")
fi

ok_json() {
    # JsonResponse 출력: {"status": "ok", "db": "ok", "message_classifier": {"status": "ok", "version": "v2"}}
    [[ "$1" == *'"status": "ok", "db": "ok"'* ]] && [[ "$1" == *'"message_classifier": {"status": "ok"'* ]]
}

for i in $(seq 1 "$RETRIES"); do
    body="$("${CURL[@]}" 2>&1)" && ok_json "$body" && { echo "[public] 통과 ($URL): $body"; exit 0; }
    echo "[public] $i/$RETRIES 실패 ($URL): ${body:0:200}"
    [ "$i" -lt "$RETRIES" ] && sleep "$DELAY"
done
echo "[public] 프록시를 거친 공개 경로 확인 실패. 확인: docker compose --profile proxy logs --tail 50 proxy / DNS A 레코드 / 80·443 방화벽"
exit 1
