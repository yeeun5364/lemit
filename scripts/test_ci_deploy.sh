#!/usr/bin/env bash
# scripts/ci_deploy.sh 입력 검증 시나리오 (#42). CI와 PC에서:  bash scripts/test_ci_deploy.sh
# CI_DEPLOY_DRY_RUN=1로 실제 배포 대신 실행할 명령만 출력하게 해서 판정한다.
set -uo pipefail
cd "$(dirname "$0")/.."

SHA="0123456789abcdef0123456789abcdef01234567"
TAG="0123456789ab"
pass=0; fail=0

# expect <기대 출력 일부 또는 REJECT> <요청 문자열>
expect() {
    local want="$1" req="$2" out code
    out="$(SSH_ORIGINAL_COMMAND="$req" CI_DEPLOY_DRY_RUN=1 bash scripts/ci_deploy.sh 2>&1)"; code=$?
    if [ "$want" = "REJECT" ]; then
        [ "$code" -eq 2 ] && ! grep -q "DRY_RUN" <<< "$out" && ok=1 || ok=0
    else
        [ "$code" -eq 0 ] && grep -qF "$want" <<< "$out" && ok=1 || ok=0
    fi
    if [ "$ok" = 1 ]; then pass=$((pass+1)); echo "[통과] $(printf '%q' "$req") → $want"
    else fail=$((fail+1)); echo "[실패] $(printf '%q' "$req"): 기대 $want, 종료 $code, 출력: $out"; fi
}

expect "DRY_RUN: bash scripts/deploy.sh $SHA"   "deploy $SHA"
expect "DRY_RUN: bash scripts/rollback.sh $TAG" "rollback $TAG"
expect "DRY_RUN: status"                       "status"
expect REJECT ""
expect REJECT "deploy"
expect REJECT "deploy main"
expect REJECT "deploy ${SHA^^}"                 # 대문자
expect REJECT "deploy ${SHA:0:39}"              # 39자리
expect REJECT "deploy $SHA; rm -rf /"           # 명령 이어 붙이기
expect REJECT "deploy \$(id)"
expect REJECT "deploy $SHA extra"
expect REJECT "rollback $SHA"                   # 태그는 12자리
expect REJECT "rollback ../../etc"
expect REJECT "status now"
expect REJECT "bash"
expect REJECT "sh -c id"
expect REJECT $'status\nid'                     # 여러 줄
expect REJECT "deploy $(printf 'a%.0s' $(seq 1 90))"   # 너무 김

echo "=== ci_deploy 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
