#!/usr/bin/env bash
# GitHub Actions 배포 전용 SSH 키의 "강제 명령" (#42). 이 키로 접속하면 무엇을 요청하든 이 스크립트만 실행된다.
#
# 서버 ~/.ssh/authorized_keys에 배포 키를 이렇게 한 줄로 등록한다 (docs/CD.md §3):
#   command="/home/ec2-user/lemit/scripts/ci_deploy.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... lemit-github-deploy
#
# 허용하는 요청 (SSH가 SSH_ORIGINAL_COMMAND로 넘겨줌). 그 밖의 요청은 모두 거부(종료 코드 2):
#   deploy <커밋 SHA 40자리>      → scripts/deploy.sh <SHA>
#   rollback <이미지 태그 12자리>  → scripts/rollback.sh <태그>
#   status                        → 현재 태그·최근 배포 기록·컨테이너 상태·공개 경로 확인 (읽기 전용)
#
# 요청 문자열을 셸로 실행(eval)하지 않는다. 단어로 나눈 뒤 형식을 정규식으로 검사한다.
# 테스트용: CI_DEPLOY_DRY_RUN=1 이면 실행할 명령을 출력만 한다 (SSH 클라이언트는 이 환경변수를 넘길 수 없음).
set -euo pipefail

cd "$(dirname "$0")/.."
REQ="${SSH_ORIGINAL_COMMAND:-}"

reject() {
    echo "[ci_deploy] 허용하지 않는 요청입니다: $1" >&2
    echo "[ci_deploy] 사용: deploy <SHA 40자리> | rollback <태그 12자리> | status" >&2
    exit 2
}

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
        run bash scripts/deploy.sh "$ARG"
        ;;
    rollback)
        [[ "${ARG:-}" =~ ^[0-9a-f]{12}$ ]] || reject "rollback에는 이미지 태그 12자리(소문자 16진수)가 필요합니다"
        run bash scripts/rollback.sh "$ARG"
        ;;
    status)
        [ -z "${ARG:-}" ] || reject "status에는 인자가 없습니다"
        if [ "${CI_DEPLOY_DRY_RUN:-0}" = "1" ]; then echo "DRY_RUN: status"; exit 0; fi
        echo "== 현재 배포 태그: $(cat deploy/state/current_tag 2>/dev/null || echo 없음)"
        echo "== 최근 배포 기록"; tail -n 5 deploy/state/history.log 2>/dev/null || echo "(기록 없음)"
        echo "== 컨테이너"; docker compose --profile proxy ps --format '{{.Service}} {{.Status}}' || true
        echo "== 이미지"; docker image ls lemit-web --format '{{.Tag}} {{.CreatedSince}}' | head -n 5 || true
        echo "== 공개 경로"; PUBLIC_CHECK_RETRIES=1 bash scripts/check_public.sh || true
        ;;
    *)
        reject "${ACTION:-빈 요청}"
        ;;
esac
