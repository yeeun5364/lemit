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

docker image inspect "lemit-web:$TAG" > /dev/null 2>&1 || { echo "이미지 lemit-web:$TAG 가 이 서버에 없습니다."; exit 1; }

echo "[rollback] web을 lemit-web:$TAG 로 되돌립니다"
IMAGE_TAG="$TAG" "${COMPOSE[@]}" up -d --wait --no-build web
IMAGE_TAG="$TAG" "${COMPOSE[@]}" exec -T web python scripts/smoke_test.py

echo "$TAG" > "$STATE_DIR/current_tag"
printf '%s\t%s\t%s\n' "$(date '+%F %T %Z')" "$TAG" "MANUAL_ROLLBACK_OK" >> "$STATE_DIR/history.log"
echo "[rollback] 완료: $TAG"
