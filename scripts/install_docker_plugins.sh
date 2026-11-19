#!/usr/bin/env bash
# Docker Compose·Buildx 플러그인 설치 (#7, Amazon Linux 2023). 서버의 저장소 폴더에서:
#   sudo bash scripts/install_docker_plugins.sh
#
# - Amazon Linux 2023의 dnf 저장소에는 두 플러그인이 없어 GitHub 릴리스에서 고정 버전을 받는다.
# - **두 파일의 sha256 체크섬이 모두 맞을 때만 설치한다** (PR #41 리뷰). 하나라도 실패하면 아무것도 설치하지 않고 종료 코드 1.
# - 알 수 없는 CPU 아키텍처면 즉시 중단한다.
#
# 테스트용 환경변수 (평소에는 쓰지 않음)
#   PLUGINS_DIR       설치 위치 (기본 /usr/local/lib/docker/cli-plugins)
#   ARCH_OVERRIDE     uname -m 대신 쓸 값
#   PLUGIN_SOURCE_DIR 내려받는 대신 이 폴더의 파일 사용 (체크섬 검증은 그대로)
set -euo pipefail

COMPOSE_VERSION="v5.6.1"   # 2026-10-21
BUILDX_VERSION="v0.38.1"   # 2026-10-20
PLUGINS_DIR="${PLUGINS_DIR:-/usr/local/lib/docker/cli-plugins}"
ARCH="${ARCH_OVERRIDE:-$(uname -m)}"

case "$ARCH" in
    x86_64) C_ARCH=x86_64; B_ARCH=amd64 ;;
    aarch64) C_ARCH=aarch64; B_ARCH=arm64 ;;
    *) echo "[plugins] 지원하지 않는 아키텍처: $ARCH (x86_64, aarch64만 지원). 설치하지 않습니다."; exit 1 ;;
esac

COMPOSE_FILE="docker-compose-linux-${C_ARCH}"
BUILDX_FILE="buildx-${BUILDX_VERSION}.linux-${B_ARCH}"
COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}"
BUILDX_URL="https://github.com/docker/buildx/releases/download/${BUILDX_VERSION}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

fetch() {  # fetch <URL 폴더> <파일 이름>
    if [ -n "${PLUGIN_SOURCE_DIR:-}" ]; then cp "${PLUGIN_SOURCE_DIR}/$2" .
    else curl -fsSLO "$1/$2"; fi
}

echo "[plugins] Compose ${COMPOSE_VERSION}, Buildx ${BUILDX_VERSION} (${ARCH}) 내려받기"
fetch "$COMPOSE_URL" "$COMPOSE_FILE"
fetch "$COMPOSE_URL" "${COMPOSE_FILE}.sha256"
fetch "$BUILDX_URL" "$BUILDX_FILE"
fetch "$BUILDX_URL" "checksums.txt"

# 체크섬: 두 파일 모두 통과해야 다음 단계로 간다 (set -e + pipefail로 어느 단계든 실패하면 즉시 종료)
echo "[plugins] 체크섬 검증"
sha256sum -c "${COMPOSE_FILE}.sha256"
grep -E "[ *]${BUILDX_FILE}\$" checksums.txt > buildx.sha256 || true
[ -s buildx.sha256 ] || { echo "[plugins] checksums.txt에 ${BUILDX_FILE} 항목이 없습니다. 설치하지 않습니다."; exit 1; }
sha256sum -c buildx.sha256

echo "[plugins] 설치: ${PLUGINS_DIR}"
mkdir -p "$PLUGINS_DIR"
install -m 0755 "$COMPOSE_FILE" "${PLUGINS_DIR}/docker-compose"
install -m 0755 "$BUILDX_FILE" "${PLUGINS_DIR}/docker-buildx"
echo "[plugins] 완료. 확인:  docker compose version && docker buildx version"
