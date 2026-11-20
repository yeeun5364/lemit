#!/usr/bin/env bash
# GitHub Actions 배포 전용 SSH 키의 "강제 명령" (#42, #56). 이 키로 접속하면 무엇을 요청하든 이 스크립트만 실행된다.
#
# ** 저장소 폴더 밖의 고정 경로(root 소유)에 설치해서 쓴다 (#56, PR #58 리뷰 P1). **
#   저장소 안의 파일을 강제 명령으로 쓰면, 예전 커밋을 배포(git checkout)하는 순간 강제 명령도 예전 버전으로
#   바뀐다(검사가 빠진 버전이 될 수 있음). 그래서 저장소 안에서 실행하면 거부한다.
#   설치·갱신: 서버에서 관리자가  sudo bash scripts/install_ci_deploy.sh   (docs/CD.md §3-2)
#   authorized_keys 한 줄:
#   command="/usr/local/bin/lemit-ci-deploy",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 AAAA... lemit-github-deploy
#
# 허용하는 요청 (SSH가 SSH_ORIGINAL_COMMAND로 넘겨줌). 그 밖의 요청은 모두 거부(종료 코드 2):
#   deploy <커밋 SHA 40자리>      → origin/main에 포함된 커밋인지 확인 후 scripts/deploy.sh <SHA>
#   rollback <이미지 태그 12자리>  → scripts/rollback.sh <태그>
#   status                        → 현재 태그·최근 배포 기록·컨테이너 상태·공개 경로·강제 명령 버전 (읽기 전용)
#
# 요청 문자열을 셸로 실행(eval)하지 않는다. 단어로 나눈 뒤 형식을 정규식으로 검사한다.
# deploy의 main 포함 확인: 배포 키가 유출되거나 러너가 오염돼도 main 밖의 커밋(다른 브랜치·임의 커밋)은 배포되지 않는다.
#
# 환경변수 (SSH 클라이언트는 환경변수를 넘길 수 없으므로 배포 키로는 바꿀 수 없음. 테스트용):
#   CI_DEPLOY_REPO         저장소 폴더 (기본 /home/ec2-user/lemit)
#   CI_DEPLOY_DRY_RUN=1    실행할 명령을 출력만 한다
#   CI_DEPLOY_SKIP_FETCH=1 main 포함 확인 전에 git fetch를 하지 않는다
#   CI_DEPLOY_MAIN_REF     main 포함 확인 기준 (기본 refs/remotes/origin/main)
set -euo pipefail

REPO="${CI_DEPLOY_REPO:-/home/ec2-user/lemit}"
REQ="${SSH_ORIGINAL_COMMAND:-}"

reject() {
    echo "[ci_deploy] 허용하지 않는 요청입니다: $1" >&2
    echo "[ci_deploy] 사용: deploy <SHA 40자리> | rollback <태그 12자리> | status" >&2
    exit 2
}
fail() {
    echo "[ci_deploy] $1" >&2
    exit 1
}

# 저장소 안에서 실행되면 거부 (고정 경로 설치 필요)
SELF="$(readlink -f "$0")"
REPO_REAL="$(readlink -f "$REPO" 2> /dev/null || true)"
[ -n "$REPO_REAL" ] && [ -e "$REPO_REAL/.git" ] || fail "저장소 폴더가 없습니다: $REPO"
case "$SELF" in
    "$REPO_REAL"/*) fail "강제 명령은 저장소 밖의 고정 경로에 설치해야 합니다 (sudo bash scripts/install_ci_deploy.sh, docs/CD.md §3-2)" ;;
esac
cd "$REPO_REAL"

[ "${#REQ}" -le 80 ] || reject "너무 깁니다"
[[ "$REQ" != *$'\n'* && "$REQ" != *$'\r'* ]] || reject "여러 줄"
read -r ACTION ARG EXTRA <<< "$REQ" || true
[ -z "${EXTRA:-}" ] || reject "인자가 너무 많습니다"

run() {
    if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: $*"; exit 0; fi
    exec "$@"
}

case "${ACTION:-}" in
    deploy)
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        MAIN_REF="${CI_DEPLOY_MAIN_REF:-refs/remotes/origin/main}"
        if [ "${CI_DEPLOY_SKIP_FETCH:-0}" != "1" ]; then
            # 대상 ref를 명시해 원격 추적 ref를 반드시 갱신한다 (fetch 설정과 무관하게)
            git fetch --quiet origin "+refs/heads/main:refs/remotes/origin/main" || fail "git fetch 실패 (서버 네트워크 확인)"
        fi
        if ! git cat-file -e "${ARG}^{commit}" 2> /dev/null || ! git merge-base --is-ancestor "$ARG" "$MAIN_REF"; then
            reject "main에 포함되지 않은 커밋입니다"
        fi
        run bash scripts/deploy.sh "$ARG"
        ;;
    rollback)
        [[ "${ARG:-}" =~ ^[0-9a-f]{12}$ ]] || reject "rollback에는 이미지 태그 12자리(소문자 16진수)가 필요합니다"
        run bash scripts/rollback.sh "$ARG"
        ;;
    status)
        [ -z "${ARG:-}" ] || reject "status에는 인자가 없습니다"
        if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: status"; exit 0; fi
        echo "== 현재 배포 태그: $(cat deploy/state/current_tag 2> /dev/null || echo 없음)"
        echo "== 최근 배포 기록"; tail -n 5 deploy/state/history.log 2> /dev/null || echo "(기록 없음)"
        echo "== 컨테이너"; docker compose --profile proxy ps --format '{{.Service}} {{.Status}}' || true
        echo "== 이미지"; docker image ls lemit-web --format '{{.Tag}} {{.CreatedSince}}' | head -n 5 || true
        echo "== 공개 경로"; PUBLIC_CHECK_RETRIES=1 bash scripts/check_public.sh || true
        # 설치된 강제 명령이 저장소의 최신 버전과 같은지 (다르면 관리자가 install_ci_deploy.sh로 갱신)
        if cmp -s "$SELF" scripts/ci_deploy.sh; then echo "== 강제 명령: 저장소 버전과 같음"
        else echo "== 강제 명령: 저장소의 scripts/ci_deploy.sh와 다름 (갱신 필요할 수 있음, docs/CD.md §3-2)"; fi
        ;;
    *)
        reject "${ACTION:-빈 요청}"
        ;;
esac
