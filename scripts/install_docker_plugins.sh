#!/usr/bin/env bash
# Docker Compose·Buildx 플러그인 설치 (#7, #48, Amazon Linux 2023). 서버의 저장소 폴더에서:
#   sudo bash scripts/install_docker_plugins.sh                # 둘 다
#   sudo bash scripts/install_docker_plugins.sh --only buildx  # buildx만 (Compose를 따로 설치한 경우)
#   sudo bash scripts/install_docker_plugins.sh --only compose # Compose만
#
# - Amazon Linux 2023의 docker 패키지에는 **buildx 0.12.x가 함께 들어 있지만**(/usr/libexec/docker/cli-plugins)
#   Compose v5의 `compose build`는 buildx 0.17.0 이상이 필요하다 (#48). 그래서 고정 버전을 GitHub 릴리스에서 받아
#   우선순위가 더 높은 /usr/local/lib/docker/cli-plugins에 설치한다. docker 패키지 파일은 건드리지 않는다.
#   docker CLI의 플러그인 검색 순서: ~/.docker/cli-plugins → /usr/local/lib → /usr/local/libexec → /usr/lib → /usr/libexec
# - **받은 파일의 sha256 체크섬이 모두 맞을 때만 설치한다** (PR #41 리뷰). 하나라도 실패하면 아무것도 설치하지 않고 종료 코드 1.
# - 알 수 없는 CPU 아키텍처면 즉시 중단한다.
#
# 테스트용 환경변수 (평소에는 쓰지 않음)
#   PLUGINS_DIR       설치 위치 (기본 /usr/local/lib/docker/cli-plugins)
#   ARCH_OVERRIDE     uname -m 대신 쓸 값
#   PLUGIN_SOURCE_DIR 내려받는 대신 이 폴더의 파일 사용 (체크섬 검증은 그대로)
set -euo pipefail

COMPOSE_VERSION="v5.6.1"   # 2026-10-21
BUILDX_VERSION="v0.38.1"   # 2026-10-20 (Compose v5의 build는 buildx 0.17.0 이상 필요)
PLUGINS_DIR="${PLUGINS_DIR:-/usr/local/lib/docker/cli-plugins}"
ARCH="${ARCH_OVERRIDE:-$(uname -m)}"

WANT_COMPOSE=1
WANT_BUILDX=1
case "${1:-}" in
    "") ;;
    --only)
        case "${2:-}" in
            buildx) WANT_COMPOSE=0 ;;
            compose) WANT_BUILDX=0 ;;
            *) echo "[plugins] 사용법: --only buildx | --only compose"; exit 1 ;;
        esac ;;
    *) echo "[plugins] 알 수 없는 옵션: $1 (사용법: [--only buildx|compose])"; exit 1 ;;
esac

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

# 1) 내려받기
if [ "$WANT_COMPOSE" = 1 ]; then
    echo "[plugins] Compose ${COMPOSE_VERSION} (${ARCH}) 내려받기"
    fetch "$COMPOSE_URL" "$COMPOSE_FILE"
    fetch "$COMPOSE_URL" "${COMPOSE_FILE}.sha256"
fi
if [ "$WANT_BUILDX" = 1 ]; then
    echo "[plugins] Buildx ${BUILDX_VERSION} (${ARCH}) 내려받기"
    fetch "$BUILDX_URL" "$BUILDX_FILE"
    fetch "$BUILDX_URL" "checksums.txt"
fi

# 2) 체크섬: 받은 파일이 모두 통과해야 다음 단계로 간다 (set -e + pipefail로 어느 단계든 실패하면 즉시 종료)
echo "[plugins] 체크섬 검증"
if [ "$WANT_COMPOSE" = 1 ]; then sha256sum -c "${COMPOSE_FILE}.sha256"; fi
if [ "$WANT_BUILDX" = 1 ]; then
    grep -E "[ *]${BUILDX_FILE}\$" checksums.txt > buildx.sha256 || true
    [ -s buildx.sha256 ] || { echo "[plugins] checksums.txt에 ${BUILDX_FILE} 항목이 없습니다. 설치하지 않습니다."; exit 1; }
    sha256sum -c buildx.sha256
fi

# 3) 설치
echo "[plugins] 설치: ${PLUGINS_DIR}"
mkdir -p "$PLUGINS_DIR"
if [ "$WANT_COMPOSE" = 1 ]; then install -m 0755 "$COMPOSE_FILE" "${PLUGINS_DIR}/docker-compose"; fi
if [ "$WANT_BUILDX" = 1 ]; then install -m 0755 "$BUILDX_FILE" "${PLUGINS_DIR}/docker-buildx"; fi

# 4) 설치한 파일 자체의 버전 확인 + 우선순위가 더 높은 위치에 다른 플러그인이 있으면 경고
if [ "$WANT_BUILDX" = 1 ]; then echo "[plugins] $("${PLUGINS_DIR}/docker-buildx" version)"; fi
if [ "$WANT_COMPOSE" = 1 ]; then echo "[plugins] $("${PLUGINS_DIR}/docker-compose" version)"; fi
for user_home in /root "/home/${SUDO_USER:-}"; do
    for name in docker-buildx docker-compose; do
        if [ -e "$user_home/.docker/cli-plugins/$name" ]; then
            echo "[plugins] 주의: $user_home/.docker/cli-plugins/$name 이 이번 설치보다 우선합니다. 필요 없으면 직접 정리하세요."
        fi
    done
done
echo "[plugins] 완료. 확인 (일반 사용자로):  docker buildx version && docker compose version"
