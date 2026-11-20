#!/usr/bin/env bash
# 배포 전용 SSH 키의 강제 명령을 저장소 밖 고정 경로에 설치·갱신한다 (#56). 서버에서 관리자가 직접:
#   cd ~/lemit && git fetch origin && git checkout --detach origin/main   (검토한 버전)
#   sudo bash scripts/install_ci_deploy.sh
#
# - 설치 위치: /usr/local/bin/lemit-ci-deploy (root 소유, 755). 배포 과정(ec2-user, 저장소 checkout)이 바꿀 수 없다.
# - 저장소의 scripts/ci_deploy.sh를 복사한다. 배포 워크플로는 이 스크립트를 자동으로 갱신하지 않는다
#   (강제 명령의 변경은 관리자가 검토 후 설치. 상태는 Deploy 워크플로 check 결과의 '강제 명령' 줄로 확인).
# - authorized_keys는 이 스크립트가 바꾸지 않는다. 등록할 줄을 안내만 한다.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${CI_DEPLOY_DEST:-/usr/local/bin/lemit-ci-deploy}"
[ "$(id -u)" -eq 0 ] || { echo "[install-ci-deploy] sudo로 실행하세요"; exit 1; }
bash -n scripts/ci_deploy.sh

if [ -f "$DEST" ] && cmp -s scripts/ci_deploy.sh "$DEST"; then
    echo "[install-ci-deploy] 이미 같은 버전입니다: $DEST"
else
    [ -f "$DEST" ] && { echo "[install-ci-deploy] 바뀌는 내용:"; diff -u "$DEST" scripts/ci_deploy.sh || true; }
    install -o root -g root -m 0755 scripts/ci_deploy.sh "$DEST"
    echo "[install-ci-deploy] 설치: $DEST ($(sha256sum "$DEST" | cut -c1-12))"
fi
ls -l "$DEST"
echo "[install-ci-deploy] authorized_keys의 배포 키 줄은 다음 형식이어야 합니다 (docs/CD.md §3-2):"
echo "  command=\"$DEST\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 AAAA... lemit-github-deploy"
