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

# buildx 버전 판정 (#48): Amazon Linux 2023 docker 패키지의 0.12.2는 필수 실패여야 한다
bx() {
    local want="$1" output="$2" name="$3" code
    BUILDX_VERSION_OUTPUT="$output" bash scripts/buildx_check.sh > /dev/null 2>&1; code=$?
    if { [ "$want" = ok ] && [ "$code" -eq 0 ]; } || { [ "$want" = bad ] && [ "$code" -ne 0 ]; }; then
        pass=$((pass+1)); echo "[통과] buildx $name → $want"
    else fail=$((fail+1)); echo "[실패] buildx $name: 기대 $want, 종료 $code"; fi
}
bx bad "github.com/docker/buildx 0.12.2-m.2 e9943654268c" "0.12.2 (AL2023 패키지)"
bx bad "github.com/docker/buildx v0.16.9 abcdef"          "0.16.9"
bx ok  "github.com/docker/buildx v0.17.0 abcdef"          "0.17.0 (경계값)"
bx ok  "github.com/docker/buildx v0.38.1 0dbe87f36ed4"    "0.38.1"
bx ok  "github.com/docker/buildx v1.0.0 abcdef"           "1.0.0"
bx bad ""                                                  "없음"
bx bad "docker: 'buildx' is not a docker command."        "명령 없음"
# server_check 전체 실행에서도 같은 판정이 나오는지. 호스트에 Docker가 없어도 buildx 분기에 도달하도록
# 가짜 docker 명령을 PATH 맨 앞에 넣는다 (Codex 리뷰 P3)
mkdir -p "$TMP/bin"
cat > "$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$1 ${2:-}" in
    "--version "*) echo "Docker version 25.0.14, build stub" ;;
    "info "*) exit 0 ;;
    "compose version") echo "Docker Compose version v5.8.0" ;;
    "buildx version") echo "github.com/docker/buildx 0.12.2-m.2 stub" ;;
    *) exit 1 ;;
esac
STUB
chmod +x "$TMP/bin/docker"
out="$(PATH="$TMP/bin:$PATH" SERVER_CHECK_SS_CMD="true" bash scripts/server_check.sh 2>&1)"
if grep -q "\[필수\] buildx 0.12.2 < 0.17.0" <<< "$out"; then pass=$((pass+1)); echo "[통과] server_check가 buildx 0.12.2를 필수 실패로 표시"
else fail=$((fail+1)); echo "[실패] server_check buildx 판정: $(grep -i buildx <<< "$out")"; fi

echo "=== server_check 포트·buildx 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
