#!/usr/bin/env bash
# 백업 복원 점검 (#44, OPERATIONS §6 "백업은 복원해 봐야 백업"). 서버의 저장소 폴더에서:
#   bash scripts/restore_check.sh [백업 파일]    (생략하면 BACKUP_DIR의 가장 최근 백업)
#
# - 같은 db 컨테이너에 **이번 실행 전용 임시 DB**(lemit_rc_<시각>_<PID>_<난수>)를 새로 만들어 백업을 복원한다.
#   **운영 DB는 읽기만** 한다.
# - 임시 DB는 **이 실행이 직접 만든 것만** 지운다(성공·실패·중단 모두). 같은 이름의 DB가 이미 있으면
#   만들기(CREATE DATABASE)가 실패하므로 아무것도 지우지 않고 끝난다. 그래서 다른 DB나 동시에 도는
#   다른 점검의 DB를 지우지 않는다 (PR #46 리뷰).
# - 주요 테이블의 행 수를 운영 DB와 비교해 같으면 통과.
# - 백업 이후 운영 DB에 새 데이터가 들어왔으면 건수가 다를 수 있다 → 백업 직후 실행하거나, 차이를 확인한다.
# - 실제 복구(운영 DB를 백업으로 되돌리기)는 데이터 손실 위험이 있어 이 스크립트가 하지 않는다 (DEPLOY.md §9).
# - 강제 종료(kill -9·서버 재부팅)로 남은 임시 DB는 자동으로 지우지 않고 다음 실행 때 경고만 한다.
#
# 환경변수: BACKUP_DIR(기본 ~/lemit-backups), RESTORE_CHECK_DB(테스트용 임시 DB 이름 지정, lemit_rc_로 시작)
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/lemit-backups}"
FILE="${1:-$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lemit-*.dump' 2>/dev/null | sort | tail -n 1)}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "[restore-check] 백업 파일이 없습니다 ($BACKUP_DIR)"; exit 1; }

get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//'; }
DB_USER="$(get POSTGRES_USER)"
DB_NAME="$(get POSTGRES_DB)"
CHECK_DB="${RESTORE_CHECK_DB:-lemit_rc_$(date '+%Y%m%d_%H%M%S')_$$_${RANDOM}}"
[[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ && "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "[restore-check] POSTGRES_USER/DB 값 확인"; exit 1; }
# 임시 DB 이름은 항상 lemit_rc_ 접두사 + 소문자·숫자·밑줄 (따옴표 없이 SQL에 넣어도 안전, 최대 63자)
[[ "$CHECK_DB" =~ ^lemit_rc_[a-z0-9_]{1,54}$ ]] || { echo "[restore-check] 임시 DB 이름 형식 오류: $CHECK_DB"; exit 1; }
[ "$DB_NAME" != "$CHECK_DB" ] || { echo "[restore-check] 운영 DB 이름이 임시 DB 이름과 같습니다"; exit 1; }

psql() { docker compose exec -T db psql -U "$DB_USER" -v ON_ERROR_STOP=1 -qAt "$@"; }

CREATED=0
cleanup() {
    # 이 실행이 CREATE DATABASE에 성공한 경우에만 지운다
    [ "$CREATED" -eq 1 ] || return 0
    if psql -d postgres -c "DROP DATABASE IF EXISTS $CHECK_DB WITH (FORCE)" > /dev/null 2>&1; then
        echo "[restore-check] 임시 DB 삭제: $CHECK_DB"
    else
        echo "[restore-check] 경고: 임시 DB $CHECK_DB 삭제 실패. 확인 후 직접 지우세요"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 이전에 강제 종료되어 남은 임시 DB가 있으면 알려만 준다 (다른 실행이 쓰는 중일 수 있으므로 지우지 않음)
LEFTOVER="$(psql -d postgres -c "SELECT string_agg(datname, ', ') FROM pg_database WHERE datname LIKE 'lemit\_rc\_%'" || true)"
[ -z "$LEFTOVER" ] || echo "[restore-check] 참고: 이미 있는 임시 DB(다른 실행 중이거나 강제 종료로 남은 것): $LEFTOVER"

echo "[restore-check] $(basename "$FILE") → 임시 DB $CHECK_DB"
if ! psql -d postgres -c "CREATE DATABASE $CHECK_DB"; then
    echo "[restore-check] 임시 DB를 만들 수 없습니다(같은 이름이 이미 있거나 권한 문제). 기존 DB는 건드리지 않았습니다"
    exit 1
fi
CREATED=1
docker compose exec -T db pg_restore -U "$DB_USER" -d "$CHECK_DB" --no-owner --exit-on-error < "$FILE"

TABLES=(accounts_user students_student inbox_conversation inbox_message django_migrations)
fail=0
printf '  %-22s %8s %8s\n' "테이블" "운영" "복원"
for t in "${TABLES[@]}"; do
    prod="$(psql -d "$DB_NAME" -c "SELECT count(*) FROM $t")"
    restored="$(psql -d "$CHECK_DB" -c "SELECT count(*) FROM $t")"
    mark=""; [ "$prod" = "$restored" ] || { mark="  ← 다름"; fail=1; }
    printf '  %-22s %8s %8s%s\n' "$t" "$prod" "$restored" "$mark"
done

if [ "$fail" -eq 0 ]; then
    echo "[restore-check] 통과: 백업을 실제로 복원할 수 있고 주요 테이블 건수가 운영 DB와 같습니다"
else
    echo "[restore-check] 건수가 다릅니다. 백업 이후 데이터가 바뀌었는지 확인하세요 (백업 직후 다시 실행)"
    exit 1
fi
