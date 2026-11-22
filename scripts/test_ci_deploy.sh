#!/usr/bin/env bash
# scripts/ci_deploy.sh 시나리오 (#42, #56). CI와 PC에서:  bash scripts/test_ci_deploy.sh
# - 임시 폴더에 가짜 원격(origin)과 서버 저장소(clone)를 만들고, 강제 명령을 저장소 밖(고정 경로 흉내)에 복사해 실행한다.
# - CI_DEPLOY_DRY_RUN=1로 실제 배포 대신 실행할 명령만 출력하게 해서 판정한다.
set -uo pipefail
cd "$(dirname "$0")/.."
SRC="$PWD/scripts/ci_deploy.sh"

T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
g() { git -c user.name=test -c user.email=test@example.invalid -c init.defaultBranch=main "$@"; }

# 가짜 원격: main에 커밋 2개, 다른 브랜치에 커밋 1개
g init -q "$T/src"
g -C "$T/src" commit -q --allow-empty -m c1; C1="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" commit -q --allow-empty -m c2; C2="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" checkout -q -b feature
g -C "$T/src" commit -q --allow-empty -m f1; FEATURE="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" checkout -q main
g clone -q --bare "$T/src" "$T/origin.git"
g clone -q "$T/origin.git" "$T/server"              # 서버 저장소 (clone 시점: main = c2)
mkdir -p "$T/server/scripts"
# clone 뒤 원격 main에 새 커밋 c3 (서버가 fetch해야 알 수 있음)
g -C "$T/src" commit -q --allow-empty -m c3; C3="$(git -C "$T/src" rev-parse HEAD)"
g -C "$T/src" push -q "$T/origin.git" main
MISSING="0123456789abcdef0123456789abcdef01234567"   # 어디에도 없는 커밋

# 고정 경로 흉내: 저장소 밖에 설치
install -m 755 "$SRC" "$T/lemit-ci-deploy"
export CI_DEPLOY_REPO="$T/server" CI_DEPLOY_DRY_RUN=1
TAG="0123456789ab"
pass=0; fail=0

# expect <기대 출력 일부 | REJECT | FAIL:문구> <요청 문자열> [실행할 스크립트]
expect() {
    local want="$1" req="$2" bin="${3:-$T/lemit-ci-deploy}" out code ok
    out="$(SSH_ORIGINAL_COMMAND="$req" bash "$bin" 2>&1)"; code=$?
    case "$want" in
        REJECT) [ "$code" -eq 2 ] && ! grep -q "DRY_RUN" <<< "$out" && ok=1 || ok=0 ;;
        FAIL:*) [ "$code" -eq 1 ] && grep -qF "${want#FAIL:}" <<< "$out" && ok=1 || ok=0 ;;
        *) [ "$code" -eq 0 ] && grep -qF "$want" <<< "$out" && ok=1 || ok=0 ;;
    esac
    if [ "$ok" = 1 ]; then pass=$((pass+1)); echo "[통과] $(printf '%q' "$req") → $want"
    else fail=$((fail+1)); echo "[실패] $(printf '%q' "$req"): 기대 $want, 종료 $code, 출력: $out"; fi
}

# main 포함 확인 (실제 fetch 경로)
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy $C2"        # clone 때부터 있던 main 커밋
expect "DRY_RUN: bash scripts/deploy.sh $C1"     "deploy $C1"        # main의 이전 커밋(조상)
expect "DRY_RUN: bash scripts/deploy.sh $C3"     "deploy $C3"        # clone 뒤 main에 올라온 커밋 → fetch로 확인
expect REJECT                                    "deploy $FEATURE"   # 다른 브랜치 커밋 (서버에 객체가 있어도)
expect REJECT                                    "deploy $MISSING"   # 없는 커밋
git -C "$T/server" fetch -q origin feature        # 서버에 그 커밋 객체가 확실히 있는 경우도
expect REJECT                                    "deploy $FEATURE"
# 원격 main이 되감기면(강제 push) 그 커밋은 더는 main이 아님
g -C "$T/src" push -q -f "$T/origin.git" "$C2:refs/heads/main"
expect REJECT                                    "deploy $C3"

# 고정 경로: 저장소 안에서 실행하면 거부
install -m 755 "$SRC" "$T/server/scripts/ci_deploy.sh"
expect "FAIL:저장소 밖의 고정 경로" "status" "$T/server/scripts/ci_deploy.sh"
CI_DEPLOY_REPO="$T/nowhere" expect "FAIL:저장소 폴더가 없습니다" "status"

# 입력 형식
SHA="$C2"
expect "DRY_RUN: bash scripts/rollback.sh $TAG" "rollback $TAG"
expect "DRY_RUN: status"                       "status"
expect REJECT ""
expect REJECT "deploy"
expect REJECT "deploy main"
expect REJECT "deploy ${SHA^^}"                 # 대문자
expect REJECT "deploy ${SHA:0:39}"              # 39자리
expect REJECT "deploy $SHA; rm -rf /"           # 명령 이어 붙이기
expect REJECT "deploy \$(id)"
expect REJECT "deploy $SHA extra"
expect REJECT "rollback $SHA"                   # 태그는 12자리
expect REJECT "rollback ../../etc"
expect REJECT "status now"
expect REJECT "bash"
expect REJECT "sh -c id"
expect REJECT $'status\nid'                     # 여러 줄
expect REJECT "deploy $(printf 'a%.0s' $(seq 1 90))"   # 너무 김
expect REJECT "deploy-auto"
expect REJECT "deploy-auto main"
expect REJECT "deploy-auto $SHA extra"
expect REJECT "deploy-auto $FEATURE"            # main 밖 커밋

# 자동 배포(deploy-auto)와 롤백 뒤 보류 (PR #58 리뷰 P2). 지금 원격 main = c2
HOLD="$T/server/deploy/state/auto_deploy_hold"
rm -f "$HOLD"
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy-auto $C2"   # 보류 없음 + main 최신 → 배포
expect "main 최신 커밋이 아니라"                 "deploy-auto $C1"   # main에 있지만 최신이 아님 → 건너뜀(0)
expect "DRY_RUN: bash scripts/rollback.sh $TAG"  "rollback $TAG"     # 롤백은 보류를 건다
if [ -f "$HOLD" ]; then pass=$((pass+1)); echo "[통과] 롤백 뒤 보류 파일 있음"; else fail=$((fail+1)); echo "[실패] 롤백 뒤 보류 파일 없음"; fi
expect "자동 배포 보류 중"                       "deploy-auto $C2"   # 보류 중 → 배포하지 않음(0)
expect "성공하면 풉니다"                         "deploy $C2"        # DRY_RUN은 배포하지 않으므로
if [ -f "$HOLD" ]; then pass=$((pass+1)); echo "[통과] 배포하지 않은 수동 배포(DRY_RUN)는 보류를 풀지 않음"
else fail=$((fail+1)); echo "[실패] 배포하지 않았는데 보류가 풀림"; fi
rm -f "$HOLD"                                   # 성공 후 해제는 아래 '실제 실행' 시험에서 확인
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy-auto $C2"   # 보류가 없으면 다시 자동 배포 가능

# 버전 태그 배포 (deploy-tag <태그> <SHA>). 지금 원격 main = c2 (c3는 되감겨 main 밖), c1은 main의 이전 커밋
g -C "$T/src" tag v0.1.1 "$C2"                                     # 가벼운 태그 → main 최신 커밋
g -C "$T/src" tag -a -m "release" v0.2.0 "$C1"                     # 주석 태그 → main의 이전 커밋
g -C "$T/src" tag v9.9.9 "$FEATURE"                                # main 밖 커밋
g -C "$T/src" push -q "$T/origin.git" v0.1.1 v0.2.0 v9.9.9
CURRENT="$T/server/deploy/state/current_tag"
rm -f "$HOLD" "$CURRENT"
expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy-tag v0.1.1 $C2"   # 정상 태그 → 그 커밋 배포
expect "DRY_RUN: bash scripts/deploy.sh $C1"     "deploy-tag v0.2.0 $C1"   # 주석 태그·main 최신이 아니어도 그 커밋
expect REJECT                                    "deploy-tag v0.1.1 $C1"   # 태그가 다른 커밋을 가리킴
expect REJECT                                    "deploy-tag v9.9.9 $FEATURE"  # main 밖 커밋 (태그는 맞음)
expect REJECT                                    "deploy-tag v7.7.7 $C2"   # origin에 없는 태그
for bad in "0.1.1 $C2" "v0.1 $C2" "v01.1.1 $C2" "v0.1.1-rc1 $C2" "v0.1.1" "$C2 v0.1.1" "v0.1.1 ${C2:0:39}" \
           "v0.1.1 $C2 extra" "v0.1.1;id $C2"; do
    expect REJECT "deploy-tag $bad"
done
expect REJECT                                    "deploy $C2 v0.1.1"       # 두 번째 인자는 deploy-tag만
expect REJECT                                    "rollback $TAG v0.1.1"
printf '%s rollback %s 이후 자동 배포 보류 (요청 1)\n' "$(date '+%F %T')" "$TAG" > "$HOLD"
expect "건너뜀: 자동 배포 보류 중"               "deploy-tag v0.1.1 $C2"   # 롤백 보류 중 → 배포하지 않음(0)
if [ -f "$HOLD" ]; then pass=$((pass+1)); echo "[통과] 태그 배포는 보류를 풀지 않음"
else fail=$((fail+1)); echo "[실패] 태그 배포가 보류를 지움"; fi
rm -f "$HOLD"
mkdir -p "$T/server/deploy/state/rollback_pending"
echo "대기" > "$T/server/deploy/state/rollback_pending/$$"            # 살아 있는 PID(이 셸)의 롤백 대기 표시
expect "건너뜀: 대기 중인 롤백"                  "deploy-tag v0.1.1 $C2"
rm -rf "$T/server/deploy/state/rollback_pending"
echo "${C2:0:12}" > "$CURRENT"
expect "건너뜀: 태그 v0.1.1 의 커밋"             "deploy-tag v0.1.1 $C2"   # 이미 배포된 커밋 → 중복 배포 안 함
rm -f "$CURRENT"
# 원격에서 태그를 다른 커밋으로 옮기면 서버도 origin 값을 따른다
g -C "$T/src" tag -f v0.1.1 "$C1" > /dev/null
g -C "$T/src" push -q -f "$T/origin.git" v0.1.1
expect REJECT                                    "deploy-tag v0.1.1 $C2"
expect "DRY_RUN: bash scripts/deploy.sh $C1"     "deploy-tag v0.1.1 $C1"

# 서버 잠금 대기: 다른 배포가 잠금을 잡고 있으면 기다렸다가 진행, 시간 안에 못 얻으면 실패
LOCK="$T/server/deploy/state/deploy.lock"
if command -v flock > /dev/null; then
    flock "$LOCK" sleep 3 &
    sleep 0.5
    CI_DEPLOY_LOCK_WAIT=1 expect "FAIL:잠금을 얻지 못했습니다" "deploy $C2"
    CI_DEPLOY_LOCK_WAIT=10 expect "기다립니다" "deploy $C2"           # 3초 뒤 잠금을 얻어 진행
    wait

    # 경합 재현 (리뷰 시나리오): 배포 A 진행 중(잠금 보유) + 수동 롤백 B 대기 + 최신 main 자동 배포 C 도착.
    # B·C가 어떤 순서로 도착하든 끝 상태는 'B 실행, C는 보류로 건너뜀'이어야 한다 (잠금을 잡는 순서는 보장되지 않음)
    race() {  # race <설명> <먼저 보낼 요청> <나중 요청>
        rm -f "$HOLD"
        flock "$LOCK" sleep 3 &                    # A: 진행 중인 배포
        local holder=$!
        sleep 0.5
        SSH_ORIGINAL_COMMAND="$2" CI_DEPLOY_LOCK_WAIT=20 bash "$T/lemit-ci-deploy" > "$T/first.out" 2>&1 &
        local p1=$!
        sleep 0.5
        SSH_ORIGINAL_COMMAND="$3" CI_DEPLOY_LOCK_WAIT=20 bash "$T/lemit-ci-deploy" > "$T/second.out" 2>&1 &
        local p2=$!
        wait "$holder" "$p1" "$p2"
        local all; all="$(cat "$T/first.out" "$T/second.out")"
        if grep -qF "DRY_RUN: bash scripts/rollback.sh $TAG" <<< "$all" && grep -qF "자동 배포 보류 중" <<< "$all" \
            && ! grep -qF "DRY_RUN: bash scripts/deploy.sh" <<< "$all"; then
            pass=$((pass+1)); echo "[통과] 경합($1): 롤백 실행, 자동 배포는 보류로 건너뜀"
        else
            fail=$((fail+1)); echo "[실패] 경합($1): $all"
        fi
    }
    race "롤백 대기 중 자동 배포 도착" "rollback $TAG" "deploy-auto $C2"
    race "자동 배포 대기 중 롤백 도착" "deploy-auto $C2" "rollback $TAG"

    # 대기 중인 롤백의 표시를 수동 배포가 지우지 않음 (PR #58 3차 리뷰 P2). 잠금을 기다리는 롤백 B의 상태
    # (살아 있는 PID의 대기 표시)를 만들어 두고, 수동 배포 D가 먼저 잠금을 잡는 순서를 결정적으로 재현한다
    PENDING_DIR="$T/server/deploy/state/rollback_pending"
    rm -f "$HOLD"; mkdir -p "$PENDING_DIR"
    sleep 30 &
    WAITER=$!
    echo "대기" > "$PENDING_DIR/$WAITER"
    expect "대기 중인 롤백이 있습니다"               "deploy $C2"        # D가 먼저 잠금 → 표시는 남김
    if [ -f "$PENDING_DIR/$WAITER" ]; then pass=$((pass+1)); echo "[통과] 수동 배포 뒤에도 대기 중인 롤백 표시 유지"
    else fail=$((fail+1)); echo "[실패] 수동 배포가 대기 중인 롤백 표시를 지움"; fi
    expect "대기 중인 롤백이 있음"                   "deploy-auto $C2"   # 자동 배포는 대기 중인 롤백 때문에 건너뜀
    kill "$WAITER" 2> /dev/null; wait "$WAITER" 2> /dev/null
    expect "DRY_RUN: bash scripts/deploy.sh $C2"     "deploy-auto $C2"   # 프로세스가 없는 표시는 오래된 것 → 정리 후 배포
    if [ ! -e "$PENDING_DIR/$WAITER" ]; then pass=$((pass+1)); echo "[통과] 오래된 대기 표시 정리"
    else fail=$((fail+1)); echo "[실패] 오래된 대기 표시가 남음"; fi

    # 자식 스크립트까지 실제로 실행 (DRY_RUN 아님, PR #58 3차 리뷰 P1). 서버 저장소의 deploy.sh·rollback.sh를
    # 잠금 코드만 진짜와 같은 가짜로 바꿔, 실행 순서를 actions.log에 남긴다
    ACTIONS="$T/actions.log"
    make_stubs() {  # make_stubs new|old
        local name take_lock
        if [ "$1" = new ]; then
            take_lock="$(sed -n '/^take_lock() {/,/^}/p' scripts/rollback.sh)"   # 이 PR의 실제 잠금 코드
        else
            # 이 기능 이전(main)의 잠금 코드 그대로: 잠금 넘겨받기를 모름
            take_lock='take_lock() {
    if command -v flock > /dev/null 2>&1; then
        exec 9> "$STATE_DIR/deploy.lock"
        flock -n 9 || { echo "[lock] 다른 배포·롤백이 진행 중입니다. 끝난 뒤 다시 실행하세요."; exit 75; }
    fi
}'
        fi
        for name in deploy rollback; do
            printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' 'cd "$(dirname "$0")/.."' 'STATE_DIR=deploy/state' \
                "$take_lock" 'take_lock' "echo \"$name \$1\" >> \"$ACTIONS\"" \
                '[ -z "${STUB_REHOLD:-}" ] || printf "%s\n" "$STUB_REHOLD" > deploy/state/auto_deploy_hold' \
                'sleep 1' '[ "${STUB_FAIL:-0}" != 1 ] || { echo "가짜 배포 실패"; exit 1; }' > "$T/server/scripts/$name.sh"
        done
        local has=old
        grep -q DEPLOY_LOCK_HELD "$T/server/scripts/deploy.sh" && has=new
        [ "$has" = "$1" ] || { echo "가짜 스크립트 생성 오류 ($1)"; exit 1; }
    }
    real() {  # real <기대 출력 일부> <요청> : 자식까지 실행, 종료 0이어야 함
        local out code
        out="$(SSH_ORIGINAL_COMMAND="$2" CI_DEPLOY_DRY_RUN=0 bash "$T/lemit-ci-deploy" 2>&1)"; code=$?
        if [ "$code" -eq 0 ] && grep -qF "$1" <<< "$out"; then pass=$((pass+1)); echo "[통과] (실행) $2 → $1"
        else fail=$((fail+1)); echo "[실패] (실행) $2: 기대 $1, 종료 $code, 출력: $out"; fi
    }
    rm -f "$HOLD" "$ACTIONS" "$CURRENT"
    make_stubs new
    # 태그 배포가 실제로 deploy.sh를 실행하고, 보류 중이면 실행하지 않는지 (DRY_RUN이 아닌 실행)
    real "태그 v0.2.0" "deploy-tag v0.2.0 $C1"
    echo "보류 (요청 9)" > "$HOLD"
    real "건너뜀: 자동 배포 보류 중" "deploy-tag v0.2.0 $C1"
    if [ "$(cat "$ACTIONS")" = "deploy $C1" ] && [ -f "$HOLD" ]; then
        pass=$((pass+1)); echo "[통과] (실행) 태그 배포 1번만 실행, 보류 중 요청은 실행 안 함, 보류 유지"
    else
        fail=$((fail+1)); echo "[실패] (실행) 태그 배포 기록: $(tr '\n' ' ' < "$ACTIONS"), 보류 $([ -f "$HOLD" ] && echo 있음 || echo 없음)"
    fi
    rm -f "$HOLD" "$ACTIONS"
    real "잡은 잠금으로 진행" "deploy $C2"
    real "잡은 잠금으로 진행" "rollback $TAG"
    rm -f "$HOLD"
    # 예전 작업 트리 + 새 강제 명령 (배포 체크아웃 전 첫 Actions 배포·롤백): 자기 잠금에 막혀 75로 끝나지 않아야 함
    make_stubs old
    real "호환 모드" "deploy $C2"
    real "호환 모드" "rollback $TAG"
    if [ "$(cat "$ACTIONS")" = "$(printf 'deploy %s\nrollback %s\ndeploy %s\nrollback %s' "$C2" "$TAG" "$C2" "$TAG")" ]; then
        pass=$((pass+1)); echo "[통과] (실행) 새·예전 작업 트리 모두 배포·롤백 스크립트가 실제로 실행됨"
    else fail=$((fail+1)); echo "[실패] (실행) 실행 기록: $(cat "$ACTIONS")"; fi

    # 보류는 수동 배포가 성공한 뒤에만 풀림 (PR #58 4차 리뷰). 새·예전 작업 트리 모두
    hold_case() {  # hold_case <설명> <new|old> <fail 0|1> <배포 중 새 보류 내용 또는 ''> <기대: kept|released|replaced>
        local out code now
        make_stubs "$2"
        printf '%s\n' "기존 보류 (요청 1)" > "$HOLD"
        out="$(SSH_ORIGINAL_COMMAND="deploy $C2" CI_DEPLOY_DRY_RUN=0 STUB_FAIL="$3" STUB_REHOLD="$4" \
            bash "$T/lemit-ci-deploy" 2>&1)"; code=$?
        now="$(cat "$HOLD" 2> /dev/null || echo '(없음)')"
        local ok=0
        case "$5" in
            kept) [ "$code" -ne 0 ] && [ "$now" = "기존 보류 (요청 1)" ] && grep -qF "보류를 유지합니다" <<< "$out" && ok=1 ;;
            released) [ "$code" -eq 0 ] && [ ! -f "$HOLD" ] && grep -qF "보류를 풉니다" <<< "$out" && ok=1 ;;
            replaced) [ "$code" -eq 0 ] && [ "$now" = "$4" ] && grep -qF "그 보류는 유지합니다" <<< "$out" && ok=1 ;;
        esac
        if [ "$ok" = 1 ]; then pass=$((pass+1)); echo "[통과] 보류($1): 종료 $code, 보류 $now"
        else fail=$((fail+1)); echo "[실패] 보류($1): 기대 $5, 종료 $code, 보류 $now, 출력: $out"; fi
    }
    hold_case "수동 배포 실패 → 유지"                 new 1 "" kept
    hold_case "수동 배포 성공 → 해제"                 new 0 "" released
    hold_case "호환 모드, 수동 배포 실패 → 유지"      old 1 "" kept
    hold_case "호환 모드, 수동 배포 성공 → 해제"      old 0 "" released
    hold_case "배포 중 새 롤백 보류 → 새 보류 유지"   new 0 "새 롤백 보류 (요청 2)" replaced
    hold_case "호환 모드, 배포 중 새 롤백 보류 → 유지" old 0 "새 롤백 보류 (요청 2)" replaced
    # 실패로 보류가 남아 있으면 자동 배포는 계속 건너뜀
    printf '%s\n' "기존 보류 (요청 1)" > "$HOLD"
    STUB_FAIL=1 CI_DEPLOY_DRY_RUN=0 SSH_ORIGINAL_COMMAND="deploy $C2" bash "$T/lemit-ci-deploy" > /dev/null 2>&1
    expect "자동 배포 보류 중"                       "deploy-auto $C2"
    rm -f "$HOLD"

    # 실제 경합: 배포 A 진행 중 + 수동 배포 D 대기 + 롤백 B 대기 → A가 끝난 뒤 D·B가 어떤 순서로 잠금을 잡든
    # 마지막이 롤백이면 보류가 확정돼 있어야 하고(리뷰 시나리오), 마지막이 수동 배포면 보류가 풀려 있어야 한다
    make_stubs new
    for i in 1 2 3; do
        rm -f "$HOLD" "$ACTIONS"; rm -rf "$PENDING_DIR"
        flock "$LOCK" sleep 2 &
        holder=$!
        sleep 0.3
        SSH_ORIGINAL_COMMAND="deploy $C2" CI_DEPLOY_DRY_RUN=0 CI_DEPLOY_LOCK_WAIT=20 bash "$T/lemit-ci-deploy" > "$T/d.out" 2>&1 &
        pd=$!
        sleep 0.3
        SSH_ORIGINAL_COMMAND="rollback $TAG" CI_DEPLOY_DRY_RUN=0 CI_DEPLOY_LOCK_WAIT=20 bash "$T/lemit-ci-deploy" > "$T/b.out" 2>&1 &
        pb=$!
        wait "$holder" "$pd" "$pb"
        last="$(tail -n 1 "$ACTIONS" | cut -d' ' -f1)"
        left="$(find "$PENDING_DIR" -type f 2> /dev/null | wc -l)"
        if [ "$(wc -l < "$ACTIONS")" -eq 2 ] && [ "$left" -eq 0 ] \
            && { { [ "$last" = rollback ] && [ -f "$HOLD" ]; } || { [ "$last" = deploy ] && [ ! -f "$HOLD" ]; }; }; then
            pass=$((pass+1)); echo "[통과] 경합(수동 배포·롤백 대기, $i회차): 마지막 $last, 보류 $([ -f "$HOLD" ] && echo 있음 || echo 없음)"
        else
            fail=$((fail+1)); echo "[실패] 경합(수동 배포·롤백 대기, $i회차): 기록 $(tr '\n' ' ' < "$ACTIONS"), 보류 $([ -f "$HOLD" ] && echo 있음 || echo 없음), 남은 표시 $left"
        fi
    done
else
    echo "flock이 없어 잠금 대기·경합 시나리오를 건너뜀 (Linux에서 실행)"
fi

echo "=== ci_deploy 시나리오: 통과 $pass, 실패 $fail ==="
[ "$fail" -eq 0 ]
