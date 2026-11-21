#!/usr/bin/env bash
# scripts/ci_deploy.sh 시나리오 (#42, #56). CI와 PC에서:  bash scripts/test_ci_deploy.sh
# - 임시 폴더에 가짜 원격(origin)과 서버 저장소(clone)를 만들고, 강제 명령을 저장소 밖(고정 경로 흉내)에 복사해 실행한다.
# - CI_DEPLOY_DRY_RUN=1로 실제 배포 대신 실행할 명령만 출력하게 해서 판정한다.
set -uo pipefail
cd "$(dirname "$0")/.."
SRC="$PWD/scripts/ci_deploy.sh"

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
g() { git -c user.name=test -c user.email=test@example.invalid -c init.defaultBranch=main "$@"; }

# 가짜 원격: main에 커밋 2개, 다른 브랜치에 커밋 1개
g init -q "$T/src"
g -C "$T/src" commit -q --allow-empty -m c1; C1="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" commit -q --allow-empty -m c2; C2="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" checkout -q -b feature
g -C "$T/src" commit -q --allow-empty -m f1; FEATURE="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" checkout -q main
g clone -q --bare "$T/src" "$T/origin.git"
g clone -q "$T/origin.git" "$T/server"              # 서버 저장소 (clone 시점: main = c2)
mkdir -p "$T/server/scripts"
# clone 뒤 원격 main에 새 커밋 c3 (서버가 fetch해야 알 수 있음)
g -C "$T/src" commit -q --allow-empty -m c3; C3="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" push -q "$T/origin.git" main
MISSING="0123456789abcdef0123456789abcdef01234567"   # 어디에도 없는 커밋

# 고정 경로 흉내: 저장소 밖에 설치
install -m 755 "$SRC" "$T/lemit-ci-deploy"
export CI_DEPLOY_REPO="$T/server" CI_DEPLOY_DRY_RUN=1
TAG="0123456789ab"
pass=0; fail=0

# expect <기대 출력 일부 | REJECT | FAIL:문구> <요청 문자열> [실행할 스크립트]
expect() {
    local want="$1" req="$2" bin="${3:-$T/lemit-ci-deploy}" out code ok
    out="$(SSH_ORIGINAL_COMMAND="$req" bash "$bin" 2>&1)"; code=$?
    case "$want" in
        REJECT) [ "$code" -eq 2 ] && ! grep -q "DRY_RUN" <<< "$out" && ok=1 || ok=0 ;;
        FAIL:*) [ "$code" -eq 1 ] && grep -qF "${want#FAIL:}" <<< "$out" && ok=1 || ok=0 ;;
        *) [ "$code" -eq 0 ] && grep -qF "$want" <<< "$out" && ok=1 || ok=0 ;;
    esac
    if [ "$ok" = 1 ]; then pass=$((pass+1)); echo "[통과] $(printf '%q' "$req") → $want"
    else fail=$((fail+1)); echo "[실패] $(printf '%q' "$req"): 기대 $want, 종료 $code, 출력: $out"; fi
}

# main 포함 확인 (실제 fetch 경로)
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy $C2"        # clone 때부터 있던 main 커밋
expect "DRY_RUN: bash scripts/deploy.sh $C1"     "deploy $C1"        # main의 이전 커밋(조상)
expect "DRY_RUN: bash scripts/deploy.sh $C3"     "deploy $C3"        # clone 뒤 main에 올라온 커밋 → fetch로 확인
expect REJECT                                    "deploy $FEATURE"   # 다른 브랜치 커밋 (서버에 객체가 있어도)
expect REJECT                                    "deploy $MISSING"   # 없는 커밋
git -C "$T/server" fetch -q origin feature        # 서버에 그 커밋 객체가 확실히 있는 경우도
expect REJECT                                    "deploy $FEATURE"
# 원격 main이 되감기면(강제 push) 그 커밋은 더는 main이 아님
g -C "$T/src" push -q -f "$T/origin.git" "$C2:refs/heads/main"
expect REJECT                                    "deploy $C3"

# 고정 경로: 저장소 안에서 실행하면 거부
install -m 755 "$SRC" "$T/server/scripts/ci_deploy.sh"
expect "FAIL:저장소 밖의 고정 경로" "status" "$T/server/scripts/ci_deploy.sh"
CI_DEPLOY_REPO="$T/nowhere" expect "FAIL:저장소 폴더가 없습니다" "status"

# 입력 형식
SHA="$C2"
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
expect REJECT "deploy-auto"
expect REJECT "deploy-auto main"
expect REJECT "deploy-auto $SHA extra"
expect REJECT "deploy-auto $FEATURE"            # main 밖 커밋

# 자동 배포(deploy-auto)와 롤백 뒤 보류 (PR #58 리뷰 P2). 지금 원격 main = c2
HOLD="$T/server/deploy/state/auto_deploy_hold"
rm -f "$HOLD"
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy-auto $C2"   # 보류 없음 + main 최신 → 배포
expect "main 최신 커밋이 아니라"                 "deploy-auto $C1"   # main에 있지만 최신이 아님 → 건너뜀(0)
expect "DRY_RUN: bash scripts/rollback.sh $TAG"  "rollback $TAG"     # 롤백은 보류를 건다
if [ -f "$HOLD" ]; then pass=$((pass+1)); echo "[통과] 롤백 뒤 보류 파일 있음"; else fail=$((fail+1)); echo "[실패] 롤백 뒤 보류 파일 없음"; fi
expect "자동 배포 보류 중"                       "deploy-auto $C2"   # 보류 중 → 배포하지 않음(0)
expect "보류를 풉니다"                           "deploy $C2"        # 수동 배포가 보류를 푼다
if [ ! -f "$HOLD" ]; then pass=$((pass+1)); echo "[통과] 수동 배포 뒤 보류 풀림"; else fail=$((fail+1)); echo "[실패] 수동 배포 뒤에도 보류 남음"; fi
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy-auto $C2"   # 다시 자동 배포 가능

# 서버 잠금 대기: 다른 배포가 잠금을 잡고 있으면 기다렸다가 진행, 시간 안에 못 얻으면 실패
LOCK="$T/server/deploy/state/deploy.lock"
if command -v flock > /dev/null; then
    flock "$LOCK" sleep 3 &
    sleep 0.5
    CI_DEPLOY_LOCK_WAIT=1 expect "FAIL:잠금을 얻지 못했습니다" "deploy $C2"
    CI_DEPLOY_LOCK_WAIT=10 expect "기다립니다" "deploy $C2"           # 3초 뒤 잠금을 얻어 진행
    wait

    # 경합 재현 (리뷰 시나리오): 배포 A 진행 중(잠금 보유) + 수동 롤백 B 대기 + 최신 main 자동 배포 C 도착.
    # B·C가 어떤 순서로 도착하든 끝 상태는 'B 실행, C는 보류로 건너뜀'이어야 한다 (잠금을 잡는 순서는 보장되지 않음)
    race() {  # race <설명> <먼저 보낼 요청> <나중 요청>
        rm -f "$HOLD"
        flock "$LOCK" sleep 3 &                    # A: 진행 중인 배포
        local holder=$!
        sleep 0.5
        SSH_ORIGINAL_COMMAND="$2" CI_DEPLOY_LOCK_WAIT=20 bash "$T/lemit-ci-deploy" > "$T/first.out" 2>&1 &
        local p1=$!
        sleep 0.5
        SSH_ORIGINAL_COMMAND="$3" CI_DEPLOY_LOCK_WAIT=20 bash "$T/lemit-ci-deploy" > "$T/second.out" 2>&1 &
        local p2=$!
        wait "$holder" "$p1" "$p2"
        local all; all="$(cat "$T/first.out" "$T/second.out")"
        if grep -qF "DRY_RUN: bash scripts/rollback.sh $TAG" <<< "$all" && grep -qF "자동 배포 보류 중" <<< "$all" \
            && ! grep -qF "DRY_RUN: bash scripts/deploy.sh" <<< "$all"; then
            pass=$((pass+1)); echo "[통과] 경합($1): 롤백 실행, 자동 배포는 보류로 건너뜀"
        else
            fail=$((fail+1)); echo "[실패] 경합($1): $all"
        fi
    }
    race "롤백 대기 중 자동 배포 도착" "rollback $TAG" "deploy-auto $C2"
    race "자동 배포 대기 중 롤백 도착" "deploy-auto $C2" "rollback $TAG"
else
    echo "flock이 없어 잠금 대기·경합 시나리오를 건너뜀 (Linux에서 실행)"
fi

echo "=== ci_deploy 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
