#!/usr/bin/env bash
# 서버 준비 상태 점검 (#7). 서버의 저장소 폴더에서 실행한다:  bash scripts/server_check.sh
# 읽기만 하고 아무것도 바꾸지 않는다 (sudo 불필요). 필수 항목이 빠지면 종료 코드 1.
#
# 확인: OS·아키텍처, 메모리·스왑, 디스크, docker·compose·buildx·git·curl·python3, docker 그룹·데몬,
#       열려 있는 포트, .env 존재·권한, 운영 .env 사전 점검(scripts/preflight.sh)
set -uo pipefail
cd "$(dirname "$0")/.."

missing=0; warn=0
ok()   { printf '  [정상] %s\n' "$*"; }
bad()  { printf '  [필수] %s\n' "$*"; missing=$((missing+1)); }
note() { printf '  [주의] %s\n' "$*"; warn=$((warn+1)); }
have() { command -v "$1" > /dev/null 2>&1; }

echo "== 시스템"
. /etc/os-release 2>/dev/null && ok "OS: ${PRETTY_NAME:-알 수 없음}, 아키텍처: $(uname -m)"
mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
swap_kb=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
ok "메모리 $((mem_kb/1024))MiB, 스왑 $((swap_kb/1024))MiB"
[ "$swap_kb" -ge 1000000 ] || note "스왑이 1GiB 미만입니다. 이미지 빌드 중 메모리 부족을 막으려면 2GiB 스왑을 권장합니다 (DEPLOY.md §1)"
avail_gb=$(df -Pk . | awk 'NR==2 {print int($4/1024/1024)}')
[ "${avail_gb:-0}" -ge 10 ] && ok "디스크 여유 ${avail_gb}GiB" || note "디스크 여유가 ${avail_gb}GiB입니다 (10GiB 이상 권장)"

echo "== 도구"
for t in git curl python3; do
    have "$t" && ok "$t: $("$t" --version 2>&1 | head -1)" || bad "$t 가 없습니다 (sudo dnf install -y $t)"
done
if have docker; then
    ok "docker: $(docker --version 2>&1)"
    if docker info > /dev/null 2>&1; then ok "docker 데몬 실행 중, 현재 사용자로 접근 가능"
    else bad "docker 데몬에 접근할 수 없습니다 (sudo systemctl enable --now docker, docker 그룹 추가 후 재접속)"; fi
    docker compose version > /dev/null 2>&1 && ok "$(docker compose version 2>&1)" || bad "docker compose 플러그인이 없습니다 (DEPLOY.md §1)"
    docker buildx version > /dev/null 2>&1 && ok "buildx: $(docker buildx version 2>&1)" || bad "docker buildx 플러그인이 없습니다 (DEPLOY.md §1)"
else
    bad "docker 가 없습니다 (sudo dnf install -y docker)"
fi
id -nG 2>/dev/null | tr ' ' '\n' | grep -qx docker && ok "$(id -un) 사용자가 docker 그룹에 있음" || note "$(id -un) 사용자가 docker 그룹에 없습니다 (sudo usermod -aG docker $(id -un) 후 재접속)"

echo "== 열려 있는 포트 (이 서버에서 듣고 있는 TCP)"
if have ss; then
    ss -tln 2>/dev/null | awk 'NR>1 {print "  " $4}' | sort -u
    ss -tln 2>/dev/null | awk 'NR>1 {print $4}' | grep -Eq '(^|[^0-9.])(0\.0\.0\.0|\*|\[::\]):(5432|8000)$' \
        && bad "5432 또는 8000이 모든 주소(0.0.0.0)로 열려 있습니다. compose.dev.yaml을 쓰지 않았는지 확인" \
        || ok "5432·8000이 외부 주소로 열려 있지 않음"
else
    note "ss 명령이 없어 포트를 확인하지 못했습니다 (sudo dnf install -y iproute)"
fi
echo "  ※ 외부 방화벽(Lightsail 네트워킹: 22는 내 IP만, 80·443)은 콘솔에서 확인하세요"

echo "== 운영 설정"
if [ -f .env ]; then
    perm=$(stat -c '%a' .env 2>/dev/null || echo "?")
    [ "$perm" = "600" ] && ok ".env 권한 600" || note ".env 권한이 $perm 입니다 (chmod 600 .env)"
    if pf_out="$(bash scripts/preflight.sh .env 2>&1)"; then
        echo "$pf_out" | sed '$d' | sed 's/^/  /'   # 마지막 줄(모드 이름)은 deploy.sh용이라 생략
    else
        echo "$pf_out" | sed 's/^/  /'
        bad "운영 .env 사전 점검 실패 (위 내용 수정)"
    fi
else
    note ".env가 아직 없습니다 (DEPLOY.md §2)"
fi

echo "== 결과: 필수 누락 ${missing}건, 주의 ${warn}건"
[ "$missing" -eq 0 ]
