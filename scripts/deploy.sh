#!/usr/bin/env bash
# 운영 서버 배포 (#7). 서버의 저장소 폴더에서 실행한다:  scripts/deploy.sh [git-ref]   (기본: origin/main)
#
# 순서 (OPERATIONS §5): 운영 .env 사전 점검 → 코드 받기 → 이미지 빌드(태그 = 커밋 SHA) → check --deploy
#   → 마이그레이션 → web·proxy 교체 → 헬스체크 대기 → 스모크 테스트(모델 상태·ml.verify·추론 1건)
#   → 프록시(Caddy)를 거친 공개 경로 확인 → 성공 기록(OK)
# - 스모크 테스트가 실패하면 직전에 성공한 이미지 태그로 web을 되돌린다 (앱 + 모델이 함께 돌아감).
# - 공개 경로 확인만 실패하면 web은 정상이므로 되돌리지 않고 FAILED_PUBLIC_CHECK로 기록, current_tag는 바꾸지 않는다.
#   (DNS·인증서·방화벽·프록시 설정 문제. 이 스크립트는 DB 스키마와 프록시 설정 자체는 되돌리지 않는다, PR #40 리뷰)
#
# 주의
# - DB 스키마는 되돌리지 않는다. 이미지 롤백과 DB 롤백은 다르다. 파괴적 마이그레이션은 별도 계획 후 배포한다.
# - docker compose down -v 는 DB 볼륨을 지우므로 절대 쓰지 않는다.
# - compose.dev.yaml을 지정하지 않는다 (docker compose = compose.yaml만).
# - 연습용: DEPLOY_SKIP_GIT=1 이면 git을 건드리지 않고 현재 HEAD로 배포한다.
# - 이 스크립트는 실행 중 git checkout으로 자기 자신이 바뀔 수 있다. bash는 파일을 조금씩 읽으므로
#   본문 전체를 main()으로 감싸 먼저 다 읽은 뒤 실행하고, 마지막 줄에서 바로 끝낸다 (#42).
set -euo pipefail

main() {

    cd "$(dirname "$0")/.."
    STATE_DIR="deploy/state"
    mkdir -p "$STATE_DIR"
    HISTORY="$STATE_DIR/history.log"
    CURRENT="$STATE_DIR/current_tag"
    REF="${1:-origin/main}"
    COMPOSE=(docker compose --profile "${COMPOSE_PROFILE:-proxy}")

    # 동시 배포 방지 (#42): 수동 배포·GitHub Actions 배포·롤백이 겹치지 않게 서버에서 잠금을 건다
    take_lock() {
        if command -v flock > /dev/null 2>&1; then
            exec 9> "$STATE_DIR/deploy.lock"
            flock -n 9 || { echo "[lock] 다른 배포·롤백이 진행 중입니다. 끝난 뒤 다시 실행하세요."; exit 75; }
        else
            echo "[lock] flock이 없어 동시 실행 방지를 건너뜁니다 (Linux 서버에는 기본 설치됨)"
        fi
    }

    take_lock

    log() { printf '[deploy %s] %s\n' "$(date '+%F %T')" "$*"; }
    record() { printf '%s\t%s\t%s\n' "$(date '+%F %T %Z')" "$1" "$2" >> "$HISTORY"; }

    # 0) 운영 .env 사전 점검 (값 사이 정합성: 도메인/IP 모드별 보안 플래그·호스트·CSRF origin)
    PREFLIGHT="$(bash scripts/preflight.sh .env)" || { echo "$PREFLIGHT"; exit 1; }
    echo "$PREFLIGHT" | sed '$d'
    MODE="$(echo "$PREFLIGHT" | tail -1)"

    # 0-1) 빌드 도구 확인: Compose 빌드에는 buildx 0.17.0 이상 필요 (#48). 부족하면 해결 방법과 함께 중단.
    #      git checkout보다 먼저 한다: 이 검사 스크립트가 없는 예전 커밋을 배포해도 검사가 실행되게 (Codex 리뷰 P2)
    BX="$(bash scripts/buildx_check.sh)" || { log "$BX"; exit 1; }
    log "$BX"

    # 1) 코드 받기
    if [ "${DEPLOY_SKIP_GIT:-0}" != "1" ]; then
        if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
            log "서버 작업 폴더에 커밋되지 않은 변경이 있습니다. 확인 후 다시 실행하세요."; git status --short; exit 1
        fi
        git fetch --prune origin
        git checkout --detach "$REF"
    fi
    NEW_TAG="$(git rev-parse --short=12 HEAD)"
    PREV_TAG="$(cat "$CURRENT" 2>/dev/null || true)"
    log "배포 대상 $NEW_TAG (직전 성공 ${PREV_TAG:-없음})"

    # 2) 이미지 빌드 (태그 = 커밋 SHA)
    IMAGE_TAG="$NEW_TAG" "${COMPOSE[@]}" build web

    # 2-1) Django 배포 점검 (운영 .env로, 새 이미지에서). 모드별로 의도적으로 받아들이는 경고만 허용한다.
    #   ip    : HTTP 시연이라 W004(HSTS) W008(SSL 리다이렉트) W012(세션 쿠키 secure) W016(CSRF 쿠키 secure)
    #   domain: W004(HSTS, 인증서 안정 후 DJANGO_HSTS_SECONDS로 켬)
    case "$MODE" in ip) ALLOWED="W004 W008 W012 W016" ;; *) ALLOWED="W004" ;; esac
    CHECK_OUT="$(IMAGE_TAG="$NEW_TAG" "${COMPOSE[@]}" run --rm --no-deps web python manage.py check --deploy 2>&1)" || {
        echo "$CHECK_OUT"; log "manage.py check --deploy 오류로 중단"; record "$NEW_TAG" "FAILED_CHECK_DEPLOY"; exit 1; }
    UNEXPECTED=""
    for w in $(grep -oE 'security\.W[0-9]{3}' <<< "$CHECK_OUT" | sed 's/security\.//' | sort -u); do
        [[ " $ALLOWED " == *" $w "* ]] || UNEXPECTED="$UNEXPECTED $w"
    done
    if [ -n "$UNEXPECTED" ]; then
        echo "$CHECK_OUT"; log "허용하지 않은 배포 경고:$UNEXPECTED (모드 $MODE) — 설정을 고친 뒤 다시 배포하세요"
        record "$NEW_TAG" "FAILED_CHECK_DEPLOY"; exit 1
    fi
    log "check --deploy 통과 (모드 $MODE, 허용 경고: $ALLOWED)"

    # 3) DB 기동 + 마이그레이션 (새 이미지로 1회 실행)
    "${COMPOSE[@]}" up -d --wait db
    IMAGE_TAG="$NEW_TAG" "${COMPOSE[@]}" run --rm --no-deps web python manage.py migrate --noinput

    # 4) web 교체 + 헬스체크(healthy) 대기, 프록시 기동
    rollback() {
        if [ -n "$PREV_TAG" ] && [ "$PREV_TAG" != "$NEW_TAG" ]; then
            log "실패 → 직전 이미지 $PREV_TAG 로 되돌립니다"
            IMAGE_TAG="$PREV_TAG" "${COMPOSE[@]}" up -d --wait --no-build web \
                && IMAGE_TAG="$PREV_TAG" "${COMPOSE[@]}" exec -T web python scripts/smoke_test.py \
                && { record "$NEW_TAG" "FAILED_ROLLED_BACK_TO_$PREV_TAG"; log "롤백 완료 ($PREV_TAG)"; return; }
            record "$NEW_TAG" "FAILED_ROLLBACK_FAILED"; log "롤백도 실패했습니다. docs/DEPLOY.md '장애 대응'을 따르세요."
        else
            record "$NEW_TAG" "FAILED_NO_PREVIOUS"; log "되돌릴 직전 이미지가 없습니다 (첫 배포). 로그를 확인하세요."
        fi
    }

    if ! IMAGE_TAG="$NEW_TAG" "${COMPOSE[@]}" up -d --wait; then
        log "컨테이너가 healthy가 되지 않았습니다."
        "${COMPOSE[@]}" logs --no-color --tail 100 web || true
        rollback; exit 1
    fi

    # 5) 스모크 테스트 (healthz·모델 상태 ok·check·migrate --check·ml.verify·추론 1건)
    if ! IMAGE_TAG="$NEW_TAG" "${COMPOSE[@]}" exec -T web python scripts/smoke_test.py; then
        log "스모크 테스트 실패"
        rollback; exit 1
    fi

    # 6) 프록시(Caddy)를 거친 공개 경로 확인. 통과해야만 배포 성공(OK)으로 기록한다 (PR #40 리뷰)
    if [ "${COMPOSE_PROFILE:-proxy}" = "proxy" ]; then
        if ! bash scripts/check_public.sh; then
            record "$NEW_TAG" "FAILED_PUBLIC_CHECK"
            log "web 이미지 $NEW_TAG 는 스모크를 통과했지만 프록시를 거친 공개 경로가 응답하지 않습니다."
            log "current_tag는 ${PREV_TAG:-없음} 그대로입니다. 프록시 로그·DNS·인증서·방화벽을 확인한 뒤 scripts/deploy.sh를 다시 실행하세요."
            exit 1
        fi
    fi

    # 7) 성공 기록
    echo "$NEW_TAG" > "$CURRENT"
    record "$NEW_TAG" "OK"
    log "배포 완료: $NEW_TAG. PC 브라우저로 로그인 → 수강생 메시지 등록 → 분류 배지까지 확인하세요 (docs/DEPLOY.md §4)."
}

main "$@"; exit $?
