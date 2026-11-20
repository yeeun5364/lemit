#!/usr/bin/env bash
# 백업 복원 점검 (#44, OPERATIONS §6 "백업은 복원해 봐야 백업"). 서버의 저장소 폴더에서:
#   bash scripts/restore_check.sh [백업 파일]    (생략하면 BACKUP_DIR의 가장 최근 백업)
#
# - 같은 db 컨테이너에 **임시 DB**(lemit_restore_check)를 만들어 백업을 복원한다. **운영 DB는 읽기만** 한다.
# - 주요 테이블의 행 수를 운영 DB와 비교해 같으면 통과. 끝나면 임시 DB를 지운다(실패해도 지움).
# - 백업 이후 운영 DB에 새 데이터가 들어왔으면 건수가 다를 수 있다 → 백업 직후 실행하거나, 차이를 확인한다.
# - 실제 복구(운영 DB를 백업으로 되돌리기)는 데이터 손실 위험이 있어 이 스크립트가 하지 않는다 (DEPLOY.md §9).
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/lemit-backups}"
FILE="${1:-$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lemit-*.dump' 2>/dev/null | sort | tail -n 1)}"
[ -n "$FILE" ] && [ -f "$FILE" ] || { echo "[restore-check] 백업 파일이 없습니다 ($BACKUP_DIR)"; exit 1; }

get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//'; }
DB_USER="$(get POSTGRES_USER)"
DB_NAME="$(get POSTGRES_DB)"
CHECK_DB="lemit_restore_check"
[[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ && "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "[restore-check] POSTGRES_USER/DB 값 확인"; exit 1; }
[ "$DB_NAME" != "$CHECK_DB" ] || { echo "[restore-check] 운영 DB 이름이 임시 DB 이름과 같습니다"; exit 1; }

psql() { docker compose exec -T db psql -U "$DB_USER" -v ON_ERROR_STOP=1 -qAt "$@"; }
cleanup() { psql -d postgres -c "DROP DATABASE IF EXISTS $CHECK_DB WITH (FORCE)" > /dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[restore-check] $(basename "$FILE") → 임시 DB $CHECK_DB"
cleanup
psql -d postgres -c "CREATE DATABASE $CHECK_DB"
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
    echo "[restore-check] 통과: 백업을 복원할 수 있고 주요 테이블 건수가 운영 DB와 같습니다"
else
    echo "[restore-check] 건수가 다릅니다. 백업 이후 데이터가 바뀌었는지 확인하세요 (백업 직후 다시 실행)"
    exit 1
fi
