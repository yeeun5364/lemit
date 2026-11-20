#!/usr/bin/env bash
# docker buildx 버전이 Compose 빌드에 충분한지 검사 (#48). server_check.sh·deploy.sh가 같은 기준으로 쓴다.
#   bash scripts/buildx_check.sh      → 충분하면 종료 코드 0, 부족·없음이면 1 (해결 방법 출력)
#
# 이 프로젝트가 쓰는 Compose v5(install_docker_plugins.sh가 고정)의 `compose build`는 buildx 0.17.0 이상이 필요하다.
# 더 오래된 Compose 조합은 지원 대상이 아니므로 Compose 버전과 관계없이 같은 기준을 쓴다 (Codex 재리뷰, PR #49).
# Amazon Linux 2023의 docker 패키지에는 0.12.x가 들어 있어 그대로는 빌드가 실패한다.
# 테스트용: BUILDX_VERSION_OUTPUT에 `docker buildx version` 출력을 넣으면 그 값으로 판정한다.
set -uo pipefail

MIN="0.17.0"
out="${BUILDX_VERSION_OUTPUT-$(docker buildx version 2>/dev/null)}"
# 출력 예: "github.com/docker/buildx v0.38.1 0dbe87f..." 또는 "github.com/docker/buildx 0.12.2-m.2 e994365..."
ver="$(awk '{print $2}' <<< "$out" | sed -E 's/^v//; s/[-+].*$//')"

if [[ ! "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "buildx 없음 또는 버전을 읽을 수 없음 → sudo bash scripts/install_docker_plugins.sh --only buildx"
    exit 1
fi
if [ "$(printf '%s\n%s\n' "$MIN" "$ver" | sort -V | head -n 1)" != "$MIN" ]; then
    echo "buildx $ver < $MIN (Compose 빌드 불가) → sudo bash scripts/install_docker_plugins.sh --only buildx"
    exit 1
fi
echo "buildx $ver (>= $MIN)"
