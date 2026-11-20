#!/usr/bin/env bash
# deploy.sh·rollback.sh 동시 실행 방지(flock) 테스트 (#42). Linux(CI)에서:  bash scripts/test_deploy_lock.sh
# 다른 배포가 잠금을 잡고 있는 동안 deploy.sh·rollback.sh는 아무것도 하지 않고 종료 코드 75로 끝나야 한다.
set -uo pipefail
cd "$(dirname "$0")/.."
command -v flock > /dev/null || { echo "flock이 없어 건너뜀 (Linux에서 실행)"; exit 0; }

mkdir -p deploy/state
flock deploy/state/deploy.lock sleep 20 &
HOLDER=$!
sleep 1
pass=0; fail=0
for cmd in "bash scripts/deploy.sh" "bash scripts/rollback.sh 0123456789ab"; do
    start=$(date +%s)
    out="$($cmd 2>&1)"; code=$?
    took=$(( $(date +%s) - start ))
    if [ "$code" -eq 75 ] && grep -q "다른 배포·롤백이 진행 중" <<< "$out" && [ "$took" -lt 5 ]; then
        pass=$((pass+1)); echo "[통과] $cmd → 잠금 때문에 즉시 종료(75)"
    else
        fail=$((fail+1)); echo "[실패] $cmd → 종료 $code, ${took}초: $out"
    fi
done
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
echo "=== 배포 잠금: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
