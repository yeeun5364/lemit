#!/usr/bin/env bash
# 이전 이미지로 수동 롤백 (#7):  scripts/rollback.sh <이미지 태그>
# 태그 목록:  docker image ls lemit-web   /   배포 기록:  cat deploy/state/history.log
#
# 앱 이미지 안에 모델 파일이 들어 있으므로 앱과 모델이 함께 이전 버전으로 돌아간다 (OPERATIONS §5).
# DB 스키마는 되돌리지 않는다. 되돌릴 이미지가 현재 DB 스키마와 맞는지(그 사이 마이그레이션 여부) 먼저 확인한다.
set -euo pipefail

cd "$(dirname "$0")/.."
TAG="${1:?사용법: scripts/rollback.sh <이미지 태그>  (docker image ls lemit-web)}"
COMPOSE=(docker compose --profile "${COMPOSE_PROFILE:-proxy}")
STATE_DIR="deploy/state"
mkdir -p "$STATE_DIR"

# 동시 배포 방지 (#42): 수동 배포·GitHub Actions 배포·롤백이 겹치지 않게 서버에서 잠금을 건다
take_lock() {
    # 강제 명령(ci_deploy.sh)이 잠금을 기다려 잡고 넘겨준 경우: 그 잠금(설명자 9)을 그대로 쓴다 (PR #58 리뷰 P2)
    if [ "${DEPLOY_LOCK_HELD:-0}" = "1" ] && { true >&9; } 2> /dev/null && flock -n 9 2> /dev/null; then
        echo "[lock] 호출한 강제 명령이 잡은 잠금으로 진행합니다"
        return 0
    fi
    if command -v flock > /dev/null 2>&1; then
        exec 9> "$STATE_DIR/deploy.lock"
        flock -n 9 || { echo "[lock] 다른 배포·롤백이 진행 중입니다. 끝난 뒤 다시 실행하세요."; exit 75; }
    else
        echo "[lock] flock이 없어 동시 실행 방지를 건너뜁니다 (Linux 서버에는 기본 설치됨)"
    fi
}
take_lock

docker image inspect "lemit-web:$TAG" > /dev/null 2>&1 || { echo "이미지 lemit-web:$TAG 가 이 서버에 없습니다."; exit 1; }

echo "[rollback] web을 lemit-web:$TAG 로 되돌립니다"
IMAGE_TAG="$TAG" "${COMPOSE[@]}" up -d --wait --no-build web
IMAGE_TAG="$TAG" "${COMPOSE[@]}" exec -T web python scripts/smoke_test.py

echo "$TAG" > "$STATE_DIR/current_tag"
printf '%s\t%s\t%s\n' "$(date '+%F %T %Z')" "$TAG" "MANUAL_ROLLBACK_OK" >> "$STATE_DIR/history.log"
echo "[rollback] 완료: $TAG"
