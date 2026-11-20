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

echo "=== ci_deploy 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
