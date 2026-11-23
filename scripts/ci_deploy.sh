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
#                                    배포가 **성공하면** 자동 배포 보류(아래)를 푼다. 실패하면 보류 유지
#   deploy-tag <태그> <커밋 SHA>   → (버전 태그 자동 배포) 태그 vMAJOR.MINOR.PATCH가 origin에서 정확히 그 커밋을 가리키고
#                                    그 커밋이 origin/main에 포함됐는지 확인한다. 보류 중이거나 그 커밋이 이미 배포돼
#                                    있으면 '[ci_deploy] 건너뜀:'을 출력하고 아무것도 바꾸지 않고 끝낸다(0). 아니면
#                                    scripts/deploy.sh <SHA>. 보류는 풀지 않는다(수동 deploy만 품). main 최신이 아니어도 된다
#   deploy-image <태그> <커밋 SHA> <sha256:이미지 digest>
#                                  → (태그 배포 파이프라인. 지금 워크플로가 태그에 쓰는 요청) deploy-tag와 같은 태그·main
#                                    포함·보류 확인에 더해, GitHub Release <태그>의 기록(커밋·이미지 digest)이 요청과
#                                    같은지 확인한다. 작업 트리를 그 커밋으로 바꾼 뒤 그 커밋의 scripts/deploy.sh를
#                                    DEPLOY_IMAGE=<저장소>@<digest>로 실행한다: 레지스트리에서 정확히 그 digest를 받아
#                                    실행하고 **서버에서 이미지를 빌드하지 않는다.** 그 커밋·digest가 이미 배포돼 있으면 건너뜀
#   deploy-auto <커밋 SHA 40자리>  → (예전 main push 자동 배포. 지금 워크플로는 쓰지 않지만 갱신 순서와 무관하게 안전하도록
#                                    남겨 둠) 보류 중이거나 origin/main 최신 커밋이 아니면 아무것도 바꾸지 않고 끝낸다(0)
#   rollback <이미지 태그 12자리>   → 자동 배포 보류를 건 뒤 scripts/rollback.sh <태그>
#   status                         → 현재 태그·자동 배포 보류(대기 중인 롤백 포함)·최근 배포 기록·컨테이너 상태·공개 경로·
#                                    강제 명령 버전 (읽기 전용)
#
# 서버 변경 직렬화와 롤백 보존 (PR #58 리뷰 P2: 대기 중인 롤백을 새 자동 배포가 밀어내지 않게):
# - GitHub Actions에서 rollback은 deploy와 다른 concurrency 그룹이라 GitHub가 대기 중인 롤백을 취소하지 않는다.
#   대신 서버에서 직렬화한다: deploy·deploy-auto·rollback은 deploy/state/deploy.lock을 최대 CI_DEPLOY_LOCK_WAIT초
#   기다려 잡고, 잡은 잠금을 deploy.sh·rollback.sh에 넘긴다(DEPLOY_LOCK_HELD=1, 파일 설명자 9).
# - 자동 배포 보류는 두 단계다 (PR #58 3차 리뷰 P2: 대기 중인 롤백의 보류를 수동 배포가 지우지 않게):
#   1) 대기 표시 deploy/state/rollback_pending/<PID>: rollback이 잠금을 기다리기 **전에** 만들고, 잠금을 얻으면 지운다.
#      수동 배포는 이것을 지우지 않는다. 그 PID 프로세스가 없으면(비정상 종료) 오래된 표시로 보고 지운다.
#   2) 확정 보류 deploy/state/auto_deploy_hold: rollback이 잠금을 잡은 **뒤**, 롤백을 실행하기 직전에 만든다.
#      수동 배포(deploy)가 **성공한 뒤에만** 풀린다 (장애 원인을 확인한 사람이 다시 배포를 시작, PR #58 4차 리뷰).
#      수동 배포는 시작할 때 본 보류 내용과 성공 후의 내용이 같을 때만 지운다 (배포 중 새 롤백이 건 보류는 남김).
#   deploy-auto는 잠금을 잡은 뒤 둘 중 하나라도 있으면 배포하지 않는다. 그래서 롤백과 자동 배포가 어떤 순서로 잠금을
#   잡든 끝 상태는 '롤백됨, 자동 배포는 건너뜀'이고, 수동 배포가 대기 중인 롤백보다 먼저 잠금을 잡아도 그 롤백이
#   실행되면서 보류가 다시 확정된다.
# - 잠금 넘겨받기를 모르는 예전 deploy.sh·rollback.sh가 작업 트리에 있으면(이 기능 이전 커밋, 또는 예전 커밋을 배포한 뒤)
#   잠금을 넘기면 자기 잠금에 막혀 75로 끝난다 (PR #58 3차 리뷰 P1). 그래서 실행할 스크립트가 DEPLOY_LOCK_HELD를
#   모르면 잠금을 놓고 실행한다(호환 모드). 그 틈에 다른 요청이 잠금을 잡으면 예전 스크립트가 75로 실패한다(겹치지는 않음).
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
#   CI_DEPLOY_RELEASE_JSON GitHub Release API 응답 대신 읽을 파일 (테스트용)
set -euo pipefail

REPO="${CI_DEPLOY_REPO:-/home/ec2-user/lemit}"
REQ="${SSH_ORIGINAL_COMMAND:-}"

reject() {
    echo "[ci_deploy] 허용하지 않는 요청입니다: $1" >&2
    echo "[ci_deploy] 사용: deploy <SHA 40자리> | deploy-tag <vX.Y.Z> <SHA 40자리> |" \
        "deploy-image <vX.Y.Z> <SHA 40자리> <sha256:digest> | rollback <태그 12자리> | status" >&2
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
PENDING_DIR="$STATE_DIR/rollback_pending"
MAIN_REF="${CI_DEPLOY_MAIN_REF:-refs/remotes/origin/main}"
# 태그 배포 이미지를 받는 레지스트리 저장소와 Release를 확인할 GitHub 저장소 (고정. 요청으로 바꿀 수 없음)
IMAGE_REPO="ghcr.io/yeeun5364/lemit-web"
GITHUB_REPO="yeeun5364/lemit"

# 1차 길이 상한 = 허용하는 요청 중 가장 긴 것 (PR #74 리뷰 P2: 예전 140은 아래 TAG_RE가 허용하는 긴 태그를 막았음)
#   "deploy-image"(12) + 공백 + 태그 최대 "v999999.999999.999999"(21) + 공백 + SHA(40) + 공백 + "sha256:"+64(71) = 147
# 형식 검사는 이 뒤의 동작별 정규식이 한다. TAG_RE나 요청 형식을 바꾸면 이 값과 test_ci_deploy.sh 경계값 시험도 함께 바꾼다.
MAX_REQ_LEN=147
[ "${#REQ}" -le "$MAX_REQ_LEN" ] || reject "너무 깁니다"
[[ "$REQ" != *$'\n'* && "$REQ" != *$'\r'* ]] || reject "여러 줄"
read -r ACTION ARG ARG2 ARG3 EXTRA <<< "$REQ" || true
[ -z "${EXTRA:-}" ] || reject "인자가 너무 많습니다"
# 두 번째 인자는 deploy-tag·deploy-image만, 세 번째 인자는 deploy-image만 받는다.
# 이후 ARG는 언제나 커밋 SHA(또는 롤백 태그), TAG_NAME은 버전 태그, DIGEST는 이미지 digest
TAG_NAME=""
DIGEST=""
if [ "${ACTION:-}" = "deploy-tag" ] || [ "${ACTION:-}" = "deploy-image" ]; then
    TAG_NAME="${ARG:-}"
    ARG="${ARG2:-}"
    if [ "$ACTION" = "deploy-image" ]; then
        DIGEST="${ARG3:-}"
    elif [ -n "${ARG3:-}" ]; then
        reject "인자가 너무 많습니다"
    fi
elif [ -n "${ARG2:-}" ] || [ -n "${ARG3:-}" ]; then
    reject "인자가 너무 많습니다"
fi
# 배포하는 버전 태그 형식: v<정수>.<정수>.<정수> (앞자리 0 없음, 각 6자리까지). 그 밖의 v* 태그는 배포하지 않는다
TAG_RE='^v(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$'

run() {
    if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: $*"; exit 0; fi
    exec "$@"
}

# 서버 스크립트를 실행한다. 잠금 넘겨받기를 아는 버전이면 잠금을 쥔 채로, 모르는 예전 버전이면 잠금을 놓고 (호환 모드)
run_script() {
    local script="$1"
    shift
    if ! grep -q 'DEPLOY_LOCK_HELD' "$script" 2> /dev/null; then
        echo "[ci_deploy] 작업 트리의 $script 는 잠금 넘겨받기 이전 버전이라 잠금을 놓고 실행합니다 (호환 모드, docs/CD.md §3-2)"
        exec 9>&-
        unset DEPLOY_LOCK_HELD
    fi
    run bash "$script" "$@"
}

# 살아 있는 롤백 대기 표시가 있으면 0. 프로세스가 없는 오래된 표시는 지운다
rollback_pending() {
    local f pid alive=1
    for f in "$PENDING_DIR"/*; do
        [ -f "$f" ] || continue
        pid="${f##*/}"
        if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2> /dev/null; then alive=0; else rm -f "$f"; fi
    done
    return "$alive"
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

# 수동 배포: deploy.sh를 (exec가 아니라) 자식으로 실행하고, 종료 코드 0일 때만 보류를 푼다.
# deploy.sh는 모든 실패(사전 점검·빌드·check --deploy·스모크 후 자동 롤백·공개 경로 확인)를 0이 아닌 코드로 끝낸다.
deploy_then_release_hold() {
    local script=scripts/deploy.sh compat=0 rc
    if ! grep -q 'DEPLOY_LOCK_HELD' "$script" 2> /dev/null; then
        echo "[ci_deploy] 작업 트리의 $script 는 잠금 넘겨받기 이전 버전이라 잠금을 놓고 실행합니다 (호환 모드, docs/CD.md §3-2)"
        compat=1
        exec 9>&-
        unset DEPLOY_LOCK_HELD
    fi
    if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: bash $script $ARG"; exit 0; fi   # 배포 안 함 → 보류 유지
    set +e
    bash "$script" "$ARG"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
        [ -z "$HOLD_ID" ] || echo "[ci_deploy] 수동 배포 실패(종료 $rc): 자동 배포 보류를 유지합니다"
        exit "$rc"
    fi
    [ -n "$HOLD_ID" ] || exit 0
    # 호환 모드에서는 배포하는 동안 잠금을 놓았으므로 다시 잡고 확인한다
    [ "$compat" = 0 ] || lock_server
    if [ -f "$HOLD" ] && [ "$(cat "$HOLD")" = "$HOLD_ID" ]; then
        rm -f "$HOLD"
        echo "[ci_deploy] 수동 배포 성공: 자동 배포 보류를 풉니다"
    else
        echo "[ci_deploy] 수동 배포 성공. 다만 배포하는 사이에 새 롤백 보류가 걸려 그 보류는 유지합니다"
    fi
    exit 0
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

# 태그가 origin에서 정확히 이 커밋을 가리키는지 (주석 태그는 커밋까지 따라감). 서버의 태그를 origin 값으로 덮어쓴다
check_tag_commit() {
    if [ "${CI_DEPLOY_SKIP_FETCH:-0}" != "1" ]; then
        git fetch --quiet --no-tags origin "+refs/tags/$TAG_NAME:refs/tags/$TAG_NAME" \
            || reject "origin에 태그 $TAG_NAME 가 없습니다"
    fi
    local target
    target="$(git rev-parse --verify --quiet "refs/tags/$TAG_NAME^{commit}" 2> /dev/null || true)"
    [ "$target" = "$ARG" ] || reject "태그 $TAG_NAME 가 커밋 $ARG 를 가리키지 않습니다 (지금: ${target:-없음})"
}

# GitHub Release <태그>에 파이프라인이 남긴 기록(커밋·이미지)이 이 요청과 같은지 (공개 API, 인증 없음).
# 배포 키가 새도 Release에 기록되지 않은 이미지(파이프라인이 검증하지 않은 digest)는 배포되지 않게 한다.
# Release 본문의 기계용 블록 (.github/workflows/deploy.yml의 release 작업이 만든다):
#   <!-- lemit-release ... tag=<태그> ... commit=<SHA> ... image=<저장소>@sha256:<digest> ... -->
check_release_record() {
    local json why
    if [ -n "${CI_DEPLOY_RELEASE_JSON:-}" ]; then
        json="$(cat "$CI_DEPLOY_RELEASE_JSON")" || fail "Release 응답 파일을 읽지 못했습니다"
    else
        command -v curl > /dev/null 2>&1 || fail "curl이 없습니다 (Release 확인 필요)"
        json="$(curl -fsSL --max-time 20 -H 'Accept: application/vnd.github+json' \
            "https://api.github.com/repos/$GITHUB_REPO/releases/tags/$TAG_NAME")" \
            || reject "GitHub Release $TAG_NAME 를 찾지 못했습니다 (파이프라인 release 단계가 끝났는지 확인)"
    fi
    command -v python3 > /dev/null 2>&1 || fail "python3가 없습니다 (Release 확인 필요)"
    why="$(printf '%s' "$json" | python3 -c '
import json, sys
tag, sha, image = sys.argv[1:4]
try:
    r = json.load(sys.stdin)
except ValueError:
    sys.exit("Release 응답이 JSON이 아닙니다")
if r.get("tag_name") != tag or r.get("draft") or r.get("prerelease"):
    sys.exit("Release가 이 태그의 정식 릴리즈가 아닙니다")
body = r.get("body") or ""
start = body.find("<!-- lemit-release")
end = body.find("-->", start + 1) if start >= 0 else -1
if start < 0 or end < 0:
    sys.exit("Release에 파이프라인 기록(lemit-release)이 없습니다")
rec = dict(line.strip().split("=", 1) for line in body[start:end].splitlines() if "=" in line)
if rec.get("tag") != tag or rec.get("commit") != sha:
    sys.exit("Release에 기록된 태그·커밋이 요청과 다릅니다")
if rec.get("image") != image:
    sys.exit("Release에 기록된 이미지 digest가 요청과 다릅니다")
' "$TAG_NAME" "$ARG" "$IMAGE_REPO@$DIGEST" 2>&1)" || reject "${why:-Release 기록 확인 실패}"
}

case "${ACTION:-}" in
    deploy-image)
        [[ "$TAG_NAME" =~ $TAG_RE ]] || reject "deploy-image에는 vMAJOR.MINOR.PATCH 형식의 태그가 필요합니다"
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy-image에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        [[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || reject "deploy-image에는 이미지 digest(sha256:64자리)가 필요합니다"
        check_tag_commit
        check_main_commit
        check_release_record
        IMAGE_REF="$IMAGE_REPO@$DIGEST"
        lock_server
        # 잠금을 잡은 뒤에 확인한다 (deploy-tag와 같음): 기다리는 사이에 롤백이 보류를 걸었거나 대기 중일 수 있다
        if [ -f "$HOLD" ]; then
            echo "[ci_deploy] 건너뜀: 자동 배포 보류 중이라 태그 $TAG_NAME 를 배포하지 않습니다 ($(head -n 1 "$HOLD"))"
            echo "[ci_deploy] 원인을 확인한 뒤 수동 배포(Actions → Deploy → deploy)로 보류를 푸세요 (docs/CD.md §5)"
            exit 0
        fi
        if rollback_pending; then
            echo "[ci_deploy] 건너뜀: 대기 중인 롤백이 있어 태그 $TAG_NAME 를 배포하지 않습니다"
            exit 0
        fi
        check_tag_commit
        if [ "$(cat "$STATE_DIR/current_tag" 2> /dev/null || true)" = "${ARG:0:12}" ] \
            && [ "$(cat "$STATE_DIR/current_image" 2> /dev/null || true)" = "$IMAGE_REF" ]; then
            echo "[ci_deploy] 건너뜀: 태그 $TAG_NAME 의 이미지($DIGEST)가 이미 배포돼 있습니다"
            exit 0
        fi
        echo "[ci_deploy] 태그 $TAG_NAME (${ARG:0:12}) 이미지 $IMAGE_REF 배포를 시작합니다 (서버에서 빌드하지 않음)"
        if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: DEPLOY_IMAGE=$IMAGE_REF bash scripts/deploy.sh $ARG"; exit 0; fi
        # 배포할 커밋의 deploy.sh로 실행한다 (지금 작업 트리의 예전 deploy.sh는 이미지 배포를 모를 수 있음)
        if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
            fail "서버 작업 폴더에 커밋되지 않은 변경이 있습니다. 관리자가 확인한 뒤 다시 실행하세요"
        fi
        git checkout --quiet --detach "$ARG" || fail "작업 트리를 ${ARG:0:12} 로 바꾸지 못했습니다"
        grep -q 'DEPLOY_IMAGE' scripts/deploy.sh \
            || fail "커밋 ${ARG:0:12} 의 scripts/deploy.sh는 이미지 배포를 지원하지 않습니다 (이 기능 이후의 커밋에 태그를 다세요)"
        export DEPLOY_IMAGE="$IMAGE_REF"
        run_script scripts/deploy.sh "$ARG"
        ;;
    deploy-tag)
        [[ "$TAG_NAME" =~ $TAG_RE ]] || reject "deploy-tag에는 vMAJOR.MINOR.PATCH 형식의 태그가 필요합니다"
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy-tag에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        check_tag_commit
        check_main_commit
        lock_server
        # 잠금을 잡은 뒤에 확인한다: 기다리는 사이에 롤백이 보류를 걸었거나 대기 중일 수 있다 (deploy-auto와 같음)
        if [ -f "$HOLD" ]; then
            echo "[ci_deploy] 건너뜀: 자동 배포 보류 중이라 태그 $TAG_NAME 를 배포하지 않습니다 ($(head -n 1 "$HOLD"))"
            echo "[ci_deploy] 원인을 확인한 뒤 수동 배포(Actions → Deploy → deploy)로 보류를 푸세요 (docs/CD.md §5)"
            exit 0
        fi
        if rollback_pending; then
            echo "[ci_deploy] 건너뜀: 대기 중인 롤백이 있어 태그 $TAG_NAME 를 배포하지 않습니다"
            exit 0
        fi
        # 기다리는 사이에 태그가 옮겨졌을 수 있으므로 잠금 안에서 한 번 더 본다
        check_tag_commit
        if [ "$(cat "$STATE_DIR/current_tag" 2> /dev/null || true)" = "${ARG:0:12}" ]; then
            echo "[ci_deploy] 건너뜀: 태그 $TAG_NAME 의 커밋(${ARG:0:12})이 이미 배포돼 있습니다"
            exit 0
        fi
        echo "[ci_deploy] 태그 $TAG_NAME (${ARG:0:12}) 배포를 시작합니다"
        run_script scripts/deploy.sh "$ARG"
        ;;
    deploy)
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        check_main_commit
        lock_server
        # 보류는 수동 배포가 **성공한 뒤에만** 푼다 (PR #58 4차 리뷰). 지금의 보류 내용을 기억해 두고,
        # 배포가 성공했을 때 그 보류가 그대로일 때만 지운다 (그사이 새 롤백이 건 보류는 남김)
        HOLD_ID=""
        if [ -f "$HOLD" ]; then
            HOLD_ID="$(cat "$HOLD")"
            echo "[ci_deploy] 자동 배포 보류 중입니다. 이 수동 배포가 성공하면 풉니다 (실패하면 유지)"
        fi
        # 대기 중인 롤백의 표시는 지우지 않는다 (그 롤백이 실행되면 보류가 다시 확정됨)
        if rollback_pending; then
            echo "[ci_deploy] 대기 중인 롤백이 있습니다. 그 롤백이 실행되면 자동 배포가 다시 보류됩니다"
        fi
        deploy_then_release_hold
        ;;
    deploy-auto)
        [[ "${ARG:-}" =~ ^[0-9a-f]{40}$ ]] || reject "deploy-auto에는 커밋 SHA 40자리(소문자 16진수)가 필요합니다"
        check_main_commit
        lock_server
        # 잠금을 잡은 뒤에 확인한다: 기다리는 사이에 롤백이 보류를 걸었거나 대기 중일 수 있다
        if [ -f "$HOLD" ]; then
            echo "[ci_deploy] 자동 배포 보류 중이라 배포하지 않습니다: $(head -n 1 "$HOLD")"
            echo "[ci_deploy] 다시 배포하려면 수동 배포(Actions → Deploy → deploy)를 실행하세요 (docs/CD.md §5)"
            exit 0
        fi
        if rollback_pending; then
            echo "[ci_deploy] 자동 배포 보류 중이라 배포하지 않습니다: 대기 중인 롤백이 있음"
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
        run_script scripts/deploy.sh "$ARG"
        ;;
    rollback)
        [[ "${ARG:-}" =~ ^[0-9a-f]{12}$ ]] || reject "rollback에는 이미지 태그 12자리(소문자 16진수)가 필요합니다"
        # 1) 잠금을 기다리기 전에 대기 표시 (그사이 잠금을 먼저 잡는 자동 배포도 멈추게, 수동 배포는 지우지 않음)
        mkdir -p "$PENDING_DIR"
        PENDING="$PENDING_DIR/$$"
        printf '%s rollback %s 대기\n' "$(date '+%F %T %Z')" "$ARG" > "$PENDING"
        trap 'rm -f "$PENDING"' EXIT
        lock_server
        # 2) 잠금 안에서 보류 확정 → 대기 표시 정리 → 롤백 (exec 뒤에는 trap이 없으므로 먼저 지운다)
        # 요청 PID를 넣어 보류마다 내용이 달라지게 한다 (수동 배포가 '자기가 본 보류'만 풀도록)
        printf '%s rollback %s 이후 자동 배포 보류 (요청 %s)\n' "$(date '+%F %T %Z')" "$ARG" "$$" > "$HOLD.tmp"
        mv -f "$HOLD.tmp" "$HOLD"
        rm -f "$PENDING"
        trap - EXIT
        echo "[ci_deploy] 자동 배포를 보류했습니다 (수동 배포로 풀림)"
        run_script scripts/rollback.sh "$ARG"
        ;;
    status)
        [ -z "${ARG:-}" ] || reject "status에는 인자가 없습니다"
        if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: status"; exit 0; fi
        echo "== 현재 배포 태그: $(cat deploy/state/current_tag 2> /dev/null || echo 없음)"
        echo "== 현재 레지스트리 이미지: $(cat deploy/state/current_image 2> /dev/null || echo '없음 (서버에서 빌드한 이미지)')"
        if [ -f "$HOLD" ]; then echo "== 자동 배포: 보류 중 ($(head -n 1 "$HOLD"))"
        elif rollback_pending; then echo "== 자동 배포: 보류 중 (대기 중인 롤백 있음)"
        else echo "== 자동 배포: 보류 없음"; fi
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
