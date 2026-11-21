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
# DEPLOY_LOCK_HELD=1만 있고 실제 잠금을 넘겨받지 않았으면 우회되지 않아야 한다
out="$(DEPLOY_LOCK_HELD=1 bash scripts/rollback.sh 0123456789ab 2>&1)"; code=$?
if [ "$code" -eq 75 ]; then pass=$((pass+1)); echo "[통과] DEPLOY_LOCK_HELD만으로는 잠금을 우회하지 못함(75)"
else fail=$((fail+1)); echo "[실패] DEPLOY_LOCK_HELD만으로 진행됨: 종료 $code, $out"; fi
# flock 명령이 띄운 sleep도 잠금을 물려받으므로 자식부터 끝낸다
pkill -P "$HOLDER" 2>/dev/null; kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null

# 강제 명령(ci_deploy.sh)처럼 호출한 쪽이 잠금을 잡고(설명자 9) 넘기면, 다시 잡으려다 75로 끝나지 않고 그 잠금으로 진행 (PR #58 리뷰 P2)
# rollback.sh만 실행한다: 잠금 다음 단계가 '없는 이미지 태그'에서 멈춰 어디서 돌려도 안전하다
# (deploy.sh는 잠금 다음에 실제 배포 단계로 넘어가므로 실행하지 않는다. 같은 take_lock 코드)
for cmd in "bash scripts/rollback.sh 0123456789ab"; do
    out="$( (exec 9> deploy/state/deploy.lock; flock -n 9 && DEPLOY_LOCK_HELD=1 $cmd) 2>&1)"; code=$?
    if [ "$code" -ne 75 ] && grep -q "잡은 잠금으로 진행" <<< "$out"; then
        pass=$((pass+1)); echo "[통과] $cmd → 넘겨받은 잠금으로 진행 (이후 단계 종료 $code)"
    else
        fail=$((fail+1)); echo "[실패] $cmd → 종료 $code: $out"
    fi
done
echo "=== 배포 잠금: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
