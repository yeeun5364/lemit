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
#   deploy <커밋 SHA 40자리>       → (수동 배포) origin/main에 포함된 커밋인지 확인 후 scripts/deploy.sh <SHA>.
#                                    자동 배포 보류(아래)를 푼다
#   deploy-auto <커밋 SHA 40자리>  → (자동 배포) 보류 중이거나 origin/main 최신 커밋이 아니면 아무것도 바꾸지 않고 끝낸다(0).
#                                    아니면 deploy와 같다
#   rollback <이미지 태그 12자리>   → 자동 배포 보류를 건 뒤 scripts/rollback.sh <태그>
#   status                         → 현재 태그·자동 배포 보류·최근 배포 기록·컨테이너 상태·공개 경로·강제 명령 버전 (읽기 전용)
#
# 서버 변경 직렬화와 롤백 보존 (PR #58 리뷰 P2: 대기 중인 롤백을 새 자동 배포가 밀어내지 않게):
# - GitHub Actions에서 rollback은 deploy와 다른 concurrency 그룹이라 GitHub가 대기 중인 롤백을 취소하지 않는다.
#   대신 서버에서 직렬화한다: deploy·deploy-auto·rollback은 deploy/state/deploy.lock을 최대 CI_DEPLOY_LOCK_WAIT초
#   기다려 잡고, 잡은 잠금을 deploy.sh·rollback.sh에 넘긴다(DEPLOY_LOCK_HELD=1, 파일 설명자 9).
# - rollback은 잠금을 기다리기 전에 자동 배포 보류(deploy/state/auto_deploy_hold)부터 건다. deploy-auto는 잠금을 잡은
#   뒤에 보류를 확인한다. 그래서 롤백과 자동 배포가 어떤 순서로 잠금을 잡든 끝 상태는 '롤백됨, 자동 배포는 건너뜀'이다.
#   보류는 수동 배포(deploy)로만 풀린다 (장애 원인을 확인한 사람이 다시 배포를 시작).
#
# 요청 문자열을 셸로 실행(eval)하지 않는다. 단어로 나눈 뒤 형식을 정규식으로 검사한다.
# deploy의 main 포함 확인: 배포 키가 유출되거나 러너가 오염돼도 main 밖의 커밋(다른 브랜치·임의 커밋)은 배포되지 않는다.
#
# 환경변수 (SSH 클라이언트는 환경변수를 넘길 수 없으므로 배포 키로는 바꿀 수 없음. 테스트용):
#   CI_DEPLOY_REPO         저장소 폴더 (기본 /home/ec2-user/lemit)
#   CI_DEPLOY_DRY_RUN=1    실행할 명령을 출력만 한다
#   CI_DEPLOY_SKIP_FETCH=1 main 포함 확인 전에 git fetch를 하지 않는다
#   CI_DEPLOY_MAIN_REF     main 포함 확인 기준 (기본 refs/remotes/origin/main)
#   CI_DEPLOY_LOCK_WAIT    서버 잠금을 기다리는 최대 초 (기본 1200)
set -euo pipefail

REPO="${CI_DEPLOY_REPO:-/home/ec2-user/lemit}"
REQ="${SSH_ORIGINAL_COMMAND:-}"

reject() {
    echo "[ci_deploy] 허용하지 않는 요청입니다: $1" >&2
    echo "[ci_deploy] 사용: deploy <SHA 40자리> | deploy-auto <SHA 40자리> | rollback <태그 12자리> | status" >&2
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

STATE_DIR="deploy/state"
HOLD="$STATE_DIR/auto_deploy_hold"
MAIN_REF="${CI_DEPLOY_MAIN_REF:-refs/remotes/origin/main}"

[ "${#REQ}" -le 80 ] || reject "너무 깁니다"
[[ "$REQ" != *$'\n'* && "$REQ" != *$'\r'* ]] || reject "여러 줄"
read -r ACTION ARG EXTRA <<< "$REQ" || true
[ -z "${EXTRA:-}" ] || reject "인자가 너무 많습니다"

run() {
    if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: $*"; exit 0; fi
    exec "$@"
}

# 서버 변경 잠금을 기다려 잡는다. 잠금(설명자 9)은 exec로 넘어가 deploy.sh·rollback.sh가 끝날 때까지 유지된다
lock_server() {
    local wait="${CI_DEPLOY_LOCK_WAIT:-1200}"
    mkdir -p "$STATE_DIR"
    command -v flock > /dev/null 2>&1 || fail "flock이 없습니다 (서버 잠금 필요)"
    exec 9> "$STATE_DIR/deploy.lock"
    if ! flock -n 9; then
        echo "[ci_deploy] 다른 배포·롤백이 진행 중입니다. 끝날 때까지 기다립니다 (최대 ${wait}초)"
        flock -w "$wait" 9 || fail "${wait}초 안에 잠금을 얻지 못했습니다. 진행 중인 배포를 확인하세요 (status)"
    fi
    export DEPLOY_LOCK_HELD=1
}

check_main_commit() {
    if [ "${CI_DEPLOY_SKIP_FETCH:-0}" != "1" ]; then
        # 대상 ref를 명시해 원격 추적 ref를 반드시 갱신한다 (fetch 설정과 무관하게)
        git fetch --quiet origin "+refs/heads/main:refs/remotes/origin/main" || fail "git fetch 실패 (서버 네트워크 확인)"
    fi
    if ! git cat-file -e "${ARG}^{commit}" 2> /dev/null || ! git merge-base --is-ancestor "$ARG" "$MAIN_REF"; then
        reject "main에 포함되지 않은 커밋입니다"
    fi
}

case "${ACTION:-}" in
    deploy)
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        check_main_commit
        lock_server
        if [ -f "$HOLD" ]; then
            rm -f "$HOLD"
            echo "[ci_deploy] 수동 배포라 자동 배포 보류를 풉니다"
        fi
        run bash scripts/deploy.sh "$ARG"
        ;;
    deploy-auto)
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy-auto에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        check_main_commit
        lock_server
        # 잠금을 잡은 뒤에 확인한다: 기다리는 사이에 롤백이 보류를 걸었을 수 있다
        if [ -f "$HOLD" ]; then
            echo "[ci_deploy] 자동 배포 보류 중이라 배포하지 않습니다: $(head -n 1 "$HOLD")"
            echo "[ci_deploy] 다시 배포하려면 수동 배포(Actions → Deploy → deploy)를 실행하세요 (docs/CD.md §5)"
            exit 0
        fi
        # 기다리는 사이에 main에 더 새 커밋이 들어왔으면 그 커밋의 자동 배포에 맡긴다
        if [ "${CI_DEPLOY_SKIP_FETCH:-0}" != "1" ]; then
            git fetch --quiet origin "+refs/heads/main:refs/remotes/origin/main" || fail "git fetch 실패 (서버 네트워크 확인)"
        fi
        if [ "$(git rev-parse "$MAIN_REF")" != "$ARG" ]; then
            echo "[ci_deploy] main 최신 커밋이 아니라 이 자동 배포는 건너뜁니다 (새 커밋의 자동 배포가 따로 실행됨)"
            exit 0
        fi
        run bash scripts/deploy.sh "$ARG"
        ;;
    rollback)
        [[ "${ARG:-}" =~ ^[0-9a-f]{12}$ ]] || reject "rollback에는 이미지 태그 12자리(소문자 16진수)가 필요합니다"
        # 잠금을 기다리기 전에 보류부터 건다 (그사이 잠금을 먼저 잡는 자동 배포도 멈추게)
        mkdir -p "$STATE_DIR"
        printf '%s rollback %s 요청 이후 자동 배포 보류\n' "$(date '+%F %T %Z')" "$ARG" > "$HOLD.tmp"
        mv -f "$HOLD.tmp" "$HOLD"
        echo "[ci_deploy] 자동 배포를 보류했습니다 (수동 배포로 풀림)"
        lock_server
        run bash scripts/rollback.sh "$ARG"
        ;;
    status)
        [ -z "${ARG:-}" ] || reject "status에는 인자가 없습니다"
        if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: status"; exit 0; fi
        echo "== 현재 배포 태그: $(cat deploy/state/current_tag 2> /dev/null || echo 없음)"
        if [ -f "$HOLD" ]; then echo "== 자동 배포: 보류 중 ($(head -n 1 "$HOLD"))"; else echo "== 자동 배포: 보류 없음"; fi
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
