#!/usr/bin/env bash
# 배포 전 운영 .env 정합성 검사 (#7, PR #40 리뷰). 문제가 있으면 종료 코드 1로 배포를 막는다.
#   scripts/preflight.sh [env 파일]   (기본: .env)
#
# 공통: .env 존재, DJANGO_DEBUG=False, 예시 비밀값(change-me) 없음, 비밀키 길이
# 도메인 모드 (SITE_ADDRESS가 도메인): DJANGO_BEHIND_PROXY=True, DJANGO_SECURE_HTTPS=True,
#   DJANGO_ALLOWED_HOSTS 첫 값 = 도메인 (컨테이너 헬스체크가 첫 값을 Host로 씀), CSRF_TRUSTED_ORIGINS에 https://도메인
# IP 모드 (SITE_ADDRESS가 :80 등): DJANGO_SECURE_HTTPS=False (HTTPS가 없으므로 켜면 리다이렉트 반복),
#   DJANGO_ALLOWED_HOSTS 첫 값이 localhost가 아님 (외부에서 접속할 서버 IP여야 함)
set -uo pipefail

ENV_FILE="${1:-.env}"
errors=()

[ -f "$ENV_FILE" ] || { echo "[preflight] $ENV_FILE 이 없습니다 (docs/DEPLOY.md §2)."; exit 1; }

get() { grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//'; }
is_true() { case "${1,,}" in true|1|yes|on) return 0 ;; *) return 1 ;; esac; }

DEBUG="$(get DJANGO_DEBUG)"
SECRET="$(get DJANGO_SECRET_KEY)"
PG_PASS="$(get POSTGRES_PASSWORD)"
SITE="$(get SITE_ADDRESS)"; SITE="${SITE:-:80}"
HOSTS="$(get DJANGO_ALLOWED_HOSTS)"
FIRST_HOST="${HOSTS%%,*}"
ORIGINS="$(get DJANGO_CSRF_TRUSTED_ORIGINS)"
BEHIND="$(get DJANGO_BEHIND_PROXY)"
HTTPS="$(get DJANGO_SECURE_HTTPS)"

is_true "$DEBUG" && errors+=("DJANGO_DEBUG가 켜져 있습니다. 운영은 False")
[ "$SECRET" = "change-me" ] || [ -z "$SECRET" ] && errors+=("DJANGO_SECRET_KEY가 비었거나 예시 값입니다")
[ "${#SECRET}" -lt 50 ] && errors+=("DJANGO_SECRET_KEY가 50자보다 짧습니다 (secrets.token_urlsafe(50) 권장)")
[ "$PG_PASS" = "change-me" ] || [ -z "$PG_PASS" ] && errors+=("POSTGRES_PASSWORD가 비었거나 예시 값입니다")
[[ "$PG_PASS" =~ ^[A-Za-z0-9_-]+$ ]] || errors+=("POSTGRES_PASSWORD에는 영문·숫자·-_ 만 씁니다 (URL에 그대로 들어감)")
[ -z "$HOSTS" ] && errors+=("DJANGO_ALLOWED_HOSTS가 비었습니다")

if [[ "$SITE" == :* ]]; then
    MODE="ip"
    is_true "$HTTPS" && errors+=("IP 모드(SITE_ADDRESS=$SITE)인데 DJANGO_SECURE_HTTPS가 켜져 있습니다. HTTPS가 없으므로 False")
    case "$FIRST_HOST" in localhost|127.0.0.1|"") errors+=("IP 모드에서는 DJANGO_ALLOWED_HOSTS 첫 값을 서버 IP로 둡니다 (현재: '$FIRST_HOST')") ;; esac
else
    MODE="domain"
    is_true "$BEHIND" || errors+=("도메인 모드($SITE)에서는 DJANGO_BEHIND_PROXY=True (Caddy가 보낸 https 표시를 믿어야 함)")
    is_true "$HTTPS" || errors+=("도메인 모드($SITE)에서는 DJANGO_SECURE_HTTPS=True (HTTPS 강제·보안 쿠키)")
    [ "$FIRST_HOST" = "$SITE" ] || errors+=("DJANGO_ALLOWED_HOSTS 첫 값이 도메인 '$SITE'가 아닙니다 (현재: '$FIRST_HOST')")
    [[ ",$ORIGINS," == *",https://$SITE,"* ]] || errors+=("DJANGO_CSRF_TRUSTED_ORIGINS에 https://$SITE 가 없습니다")
fi

if [ "${#errors[@]}" -gt 0 ]; then
    echo "[preflight] $ENV_FILE 설정 오류 ${#errors[@]}건 (모드: $MODE) — 배포를 중단합니다"
    for e in "${errors[@]}"; do echo "  - $e"; done
    exit 1
fi
echo "[preflight] 통과 (모드: $MODE, 사이트: $SITE, 첫 호스트: $FIRST_HOST)"
echo "$MODE"
