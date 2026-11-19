#!/usr/bin/env bash
# scripts/server_check.sh 포트 판정 시나리오 테스트 (#7, PR #41 리뷰). CI와 PC에서:  bash scripts/test_server_check.sh
# 실제 ss 대신 SERVER_CHECK_SS_CMD로 정해 둔 출력(ss -Htln 형식)을 넣어 판정만 확인한다.
set -uo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

# expect <exposed|safe|unknown> <이름> <ss 출력 줄...>
expect() {
    local want="$1" name="$2"; shift 2
    printf '%s\n' "$@" > "$TMP/ss.txt"
    local out
    out="$(SERVER_CHECK_SS_CMD="cat $TMP/ss.txt" bash scripts/server_check.sh 2>&1)"
    local got
    if grep -q "5432·8000이 loopback이 아닌 주소에 열려 있습니다" <<< "$out"; then got=exposed
    elif grep -q "5432·8000은 loopback" <<< "$out"; then got=safe
    elif grep -q "포트 목록을 가져오지 못했습니다" <<< "$out"; then got=unknown
    else got=none; fi
    if [ "$got" = "$want" ]; then pass=$((pass+1)); echo "[통과] $name (기대 $want)"
    else fail=$((fail+1)); echo "[실패] $name: 기대 $want, 실제 $got"; fi
}

L() { printf 'LISTEN 0 4096 %s 0.0.0.0:*' "$1"; }

expect exposed "DB가 0.0.0.0에 열림"          "$(L 0.0.0.0:5432)" "$(L 0.0.0.0:80)"
expect exposed "web이 서버 사설 IP에 열림"    "$(L 172.26.5.10:8000)"
expect exposed "web이 [::]에 열림"            "$(L '[::]:8000')"
expect exposed "DB가 *에 열림"                "$(L '*:5432')"
expect safe    "web이 127.0.0.1에만 열림"     "$(L 127.0.0.1:8000)" "$(L 0.0.0.0:80)" "$(L 0.0.0.0:443)"
expect safe    "DB가 [::1]에만 열림"          "$(L '[::1]:5432')" "$(L 0.0.0.0:22)"
expect safe    "5432·8000 없음 (80만 공개)"   "$(L 0.0.0.0:80)"

# 포트 목록을 얻지 못한 경우: 정상으로 판단하면 안 됨
out="$(SERVER_CHECK_SS_CMD="false" bash scripts/server_check.sh 2>&1)"
if grep -q "포트 목록을 가져오지 못했습니다" <<< "$out" && ! grep -q "5432·8000은 loopback" <<< "$out"; then
    pass=$((pass+1)); echo "[통과] ss 실패 시 '확인 불가' (정상으로 오판하지 않음)"
else fail=$((fail+1)); echo "[실패] ss 실패 시 판정"; fi

echo "=== server_check 포트 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
