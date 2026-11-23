#!/usr/bin/env bash
# deploy.sh의 빌드 도구(buildx) 검사 범위 (PR #74 리뷰 P2). CI와 PC에서:  bash scripts/test_deploy_buildx.sh
# - 서버에서 빌드하는 배포는 buildx가 낮으면 이미지 빌드 전에 멈춰야 한다 (#48).
# - 레지스트리 이미지 배포(DEPLOY_IMAGE)는 빌드하지 않으므로 buildx 버전과 상관없이 이미지 받기까지 가야 한다.
# 임시 저장소에 실제 deploy.sh를 복사하고, preflight·buildx_check·docker는 가짜로 바꿔 실행한다.
# 가짜 docker는 호출을 기록하고 pull·build에서 실패해 그 지점에서 배포를 멈춘다 (실제 컨테이너는 건드리지 않음).
set -uo pipefail
cd "$(dirname "$0")/.."
SRC="$PWD/scripts/deploy.sh"

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
R="$T/repo"
mkdir -p "$R/scripts" "$T/bin"
cp "$SRC" "$R/scripts/deploy.sh"
printf '#!/usr/bin/env bash\necho "[preflight] 가짜 통과"\necho ip\n' > "$R/scripts/preflight.sh"
cat > "$R/scripts/buildx_check.sh" << 'EOF'
#!/usr/bin/env bash
if [ "${FAKE_BUILDX_OK:-0}" = 1 ]; then echo "[buildx] 가짜 0.17.0 확인"; exit 0; fi
echo "[buildx] 가짜 buildx 0.12.0 < 0.17.0 (업데이트 필요)"; exit 1
EOF
cat > "$T/bin/docker" << 'EOF'
#!/usr/bin/env bash
echo "docker $*" >> "$DOCKER_LOG"
case "$*" in
    pull*) exit 1 ;;                        # 이미지 받기 단계에 도착한 것만 확인하고 멈춤
    *" build web"*) exit 1 ;;               # 서버 빌드 단계에 도착한 것만 확인하고 멈춤
    "image inspect"*) exit 1 ;;             # 같은 태그의 이전 이미지 없음
esac
exit 0
EOF
chmod +x "$T/bin/docker" "$R"/scripts/*.sh
git -C "$R" -c init.defaultBranch=main init -q
git -C "$R" -c user.name=test -c user.email=test@example.invalid add -A
git -C "$R" -c user.name=test -c user.email=test@example.invalid commit -q -m c1

IMG="ghcr.io/yeeun5364/lemit-web@sha256:$(printf 'd%.0s' $(seq 1 64))"
export DOCKER_LOG="$T/docker.log" DEPLOY_SKIP_GIT=1 PATH="$T/bin:$PATH"
pass=0; fail=0

# check <이름> <조건 결과(0=참)> <출력>
check() {
    if [ "$2" -eq 0 ]; then pass=$((pass+1)); echo "[통과] $1"
    else fail=$((fail+1)); echo "[실패] $1"; echo "$3" | sed 's/^/    /'; fi
}
run() { : > "$DOCKER_LOG"; out="$(env "$@" bash "$R/scripts/deploy.sh" 2>&1)"; code=$?; }

# 1) 이미지 배포 + buildx 낮음 → buildx로 멈추지 않고 이미지 받기까지 간다 (가짜 pull 실패로 FAILED_PULL)
run DEPLOY_IMAGE="$IMG"
grep -qF "docker pull --quiet $IMG" "$DOCKER_LOG" && ! grep -q "0.12.0" <<< "$out" && grep -q "빌드 도구 확인 건너뜀" <<< "$out" \
    && grep -q "이미지를 받지 못했습니다" <<< "$out"
check "이미지 배포는 buildx가 낮아도 이미지 받기까지 진행 (종료 $code)" $? "$out"
! grep -q " build" "$DOCKER_LOG"
check "이미지 배포는 서버에서 빌드하지 않음" $? "$(cat "$DOCKER_LOG")"

# 2) 서버 빌드 + buildx 낮음 → 빌드 전에 멈춘다 (기존 동작 #48)
run
[ "$code" -eq 1 ] && grep -q "0.12.0" <<< "$out" && [ ! -s "$DOCKER_LOG" ]
check "서버 빌드는 buildx가 낮으면 docker를 부르기 전에 중단 (종료 $code)" $? "$out"

# 3) 서버 빌드 + buildx 충분 → 검사를 통과하고 빌드 단계까지 간다
run FAKE_BUILDX_OK=1
grep -q "가짜 0.17.0 확인" <<< "$out" && grep -q " build web" "$DOCKER_LOG" && ! grep -q "^docker pull" "$DOCKER_LOG"
check "서버 빌드는 buildx 확인 뒤 빌드 단계로 진행 (종료 $code)" $? "$out"

echo "=== 배포 buildx 검사 범위: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
