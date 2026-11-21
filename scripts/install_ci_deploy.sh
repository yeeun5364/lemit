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

# 설치 폴더가 root 소유이고 다른 사용자가 쓸 수 없어야 한다 (아니면 파일을 바꿔치기할 수 있음)
DIR="$(dirname "$DEST")"
[ "$(stat -c '%u' "$DIR")" = "0" ] && [ -z "$(find "$DIR" -maxdepth 0 -perm /022)" ]     || { echo "[install-ci-deploy] $DIR 가 root 소유가 아니거나 다른 사용자가 쓸 수 있습니다. 설치하지 않습니다"; exit 1; }

if [ -f "$DEST" ] && cmp -s scripts/ci_deploy.sh "$DEST"; then
    echo "[install-ci-deploy] 내용은 이미 같은 버전입니다: $DEST (소유자·권한은 다시 맞춤)"
else
    [ -f "$DEST" ] && { echo "[install-ci-deploy] 바뀌는 내용:"; diff -u "$DEST" scripts/ci_deploy.sh || true; }
fi
# 내용이 같아도 항상 다시 설치해 소유자 root:root, 권한 755로 맞춘다 (PR #58 2차 리뷰 P3)
install -o root -g root -m 0755 scripts/ci_deploy.sh "$DEST"
[ "$(stat -c '%u:%g %a' "$DEST")" = "0:0 755" ] || { echo "[install-ci-deploy] 소유자·권한 확인 실패: $(stat -c '%U:%G %a' "$DEST")"; exit 1; }
echo "[install-ci-deploy] 설치: $DEST ($(sha256sum "$DEST" | cut -c1-12), root:root 755)"
# 사전 확인 (PR #58 3차 리뷰 P1): 작업 트리의 deploy.sh·rollback.sh가 잠금 넘겨받기(DEPLOY_LOCK_HELD)를 아는지.
# 모르는 예전 버전이어도 강제 명령은 잠금을 놓고 실행하는 호환 모드로 동작하지만, 그 사이 다른 요청과 겹치면 75로 실패할 수 있다
for s in scripts/deploy.sh scripts/rollback.sh; do
    if grep -q 'DEPLOY_LOCK_HELD' "$s"; then
        echo "[install-ci-deploy] 작업 트리 $s: 잠금 넘겨받기 지원"
    else
        echo "[install-ci-deploy] 주의: 작업 트리 $s 가 잠금 넘겨받기 이전 버전입니다. 강제 명령은 호환 모드로 동작합니다."
        echo "  권장: 위 안내대로 origin/main을 checkout한 뒤 다시 설치하세요 (docs/CD.md §3-2)"
    fi
done
echo "[install-ci-deploy] authorized_keys의 배포 키 줄은 다음 형식이어야 합니다 (docs/CD.md §3-2):"
echo "  command=\"$DEST\",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty,no-user-rc ssh-ed25519 AAAA... lemit-github-deploy"
