#!/usr/bin/env bash
# scripts/preflight.sh 시나리오 테스트 (#7, PR #40 리뷰). CI Docker 작업과 PC에서 실행한다:  bash scripts/test_preflight.sh
set -uo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SECRET="test-only-secret-$(printf 'x%.0s' $(seq 1 50))"
pass=0; fail=0

base_ip() {
cat <<EOF
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=203.0.113.10,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=
POSTGRES_PASSWORD=test-pass_123
SITE_ADDRESS=:80
DJANGO_BEHIND_PROXY=True
DJANGO_SECURE_HTTPS=False
EOF
}
base_domain() {
cat <<EOF
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=lemit.example.com,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://lemit.example.com
POSTGRES_PASSWORD=test-pass_123
SITE_ADDRESS=lemit.example.com
DJANGO_BEHIND_PROXY=True
DJANGO_SECURE_HTTPS=True
EOF
}

# expect <pass|fail> <이름> <내용 생성 명령...>
expect() {
    local want="$1" name="$2"; shift 2
    "$@" > "$TMP/env"
    if bash scripts/preflight.sh "$TMP/env" > "$TMP/out" 2>&1; then got=pass; else got=fail; fi
    if [ "$got" = "$want" ]; then pass=$((pass+1)); echo "[통과] $name (기대 $want)"
    else fail=$((fail+1)); echo "[실패] $name: 기대 $want, 실제 $got"; sed 's/^/    /' "$TMP/out"; fi
}
with() { local base="$1" key="$2" val="$3"; "$base" | sed "s|^$key=.*|$key=$val|"; }

expect pass "IP 모드 정상" base_ip
expect pass "도메인 모드 정상" base_domain
expect fail "DEBUG=True" with base_ip DJANGO_DEBUG True
expect fail "예시 비밀키" with base_ip DJANGO_SECRET_KEY change-me
expect fail "짧은 비밀키" with base_ip DJANGO_SECRET_KEY short-key
expect fail "예시 DB 비밀번호" with base_ip POSTGRES_PASSWORD change-me
expect fail "DB 비밀번호에 URL 특수문자" with base_ip POSTGRES_PASSWORD 'p@ss:word'
expect fail "IP 모드인데 HTTPS 강제" with base_ip DJANGO_SECURE_HTTPS True
expect fail "IP 모드인데 첫 호스트가 localhost" with base_ip DJANGO_ALLOWED_HOSTS localhost,203.0.113.10
expect fail "도메인 모드인데 프록시 신뢰 꺼짐" with base_domain DJANGO_BEHIND_PROXY False
expect fail "도메인 모드인데 HTTPS 강제 꺼짐" with base_domain DJANGO_SECURE_HTTPS False
expect fail "도메인이 ALLOWED_HOSTS 첫 값이 아님" with base_domain DJANGO_ALLOWED_HOSTS localhost,lemit.example.com
expect fail "CSRF origin에 https://도메인 없음" with base_domain DJANGO_CSRF_TRUSTED_ORIGINS http://lemit.example.com
expect fail "빈 .env" true

echo "=== preflight 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
