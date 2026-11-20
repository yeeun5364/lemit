#!/usr/bin/env bash
# restore_check.sh 안전성 테스트 (PR #46 리뷰 P1). db 컨테이너가 떠 있고 마이그레이션된 상태에서 실행:
#   BACKUP_DIR=... bash scripts/test_restore_check.sh
# 1) 이미 있는 DB와 이름이 겹치면 지우지 않고 실패한다 (내용 보존)
# 2) 예전 고정 이름(lemit_restore_check)의 DB를 건드리지 않는다
# 3) 동시에 3개를 실행해도 모두 통과하고, 각자 만든 임시 DB만 정리된다
# 4) 깨진 백업이면 실패하고 임시 DB를 남기지 않는다
set -euo pipefail
cd "$(dirname "$0")/.."

get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//'; }
DB_USER="$(get POSTGRES_USER)"
psql() { docker compose exec -T db psql -U "$DB_USER" -v ON_ERROR_STOP=1 -qAt "$@"; }
pass=0
ok() { echo "  PASS $1"; pass=$((pass + 1)); }
die() { echo "  FAIL $1"; exit 1; }
temp_dbs() { psql -d postgres -c "SELECT count(*) FROM pg_database WHERE datname LIKE 'lemit\_rc\_%' AND datname <> 'lemit_rc_test_existing'"; }

BACKUP_DIR="${BACKUP_DIR:?BACKUP_DIR 필요}"
export BACKUP_DIR
LATEST="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lemit-*.dump' | sort | tail -n 1)"
[ -n "$LATEST" ] || bash scripts/backup_db.sh > /dev/null
WORK="$(mktemp -d)"
MADE=()  # 이 테스트가 만든 DB만 정리한다
cleanup() {
    rm -rf "$WORK"
    for db in "${MADE[@]}"; do
        psql -d postgres -c "DROP DATABASE IF EXISTS $db WITH (FORCE)" > /dev/null 2>&1 || true
    done
}
trap cleanup EXIT

echo "[test-restore-check] 준비: 지워지면 안 되는 DB 2개 (표식 데이터 포함)"
for db in lemit_rc_test_existing lemit_restore_check; do
    [ "$(psql -d postgres -c "SELECT count(*) FROM pg_database WHERE datname='$db'")" = "0" ]         || { echo "[test-restore-check] $db DB가 이미 있어 중단합니다 (테스트는 기존 DB를 지우지 않음)"; exit 1; }
    psql -d postgres -c "CREATE DATABASE $db"
    MADE+=("$db")
    psql -d "$db" -c "CREATE TABLE marker(v text); INSERT INTO marker VALUES ('keep-me')"
done

# 1) 이름 충돌: 실패해야 하고, 기존 DB와 표식 데이터는 그대로
if RESTORE_CHECK_DB=lemit_rc_test_existing bash scripts/restore_check.sh > "$WORK/collide.log" 2>&1; then
    cat "$WORK/collide.log"; die "이름이 겹치는데 점검이 통과함"
fi
[ "$(psql -d lemit_rc_test_existing -c 'SELECT v FROM marker')" = "keep-me" ] || die "기존 DB가 지워지거나 바뀜"
grep -q "기존 DB는 건드리지 않았습니다" "$WORK/collide.log" || die "충돌 안내 문구 없음"
ok "이미 있는 DB와 이름이 겹치면 실패하고 기존 DB 보존"

# 2) 일반 실행: 예전 고정 이름 DB는 그대로
bash scripts/restore_check.sh > "$WORK/normal.log" 2>&1 || { cat "$WORK/normal.log"; die "정상 백업 점검 실패"; }
[ "$(psql -d lemit_restore_check -c 'SELECT v FROM marker')" = "keep-me" ] || die "lemit_restore_check DB가 지워짐"
[ "$(temp_dbs)" = "0" ] || die "임시 DB가 남음"
ok "정상 점검 통과, 다른 DB 보존, 자기 임시 DB 정리"

# 3) 동시 실행 3개
pids=()
for i in 1 2 3; do
    bash scripts/restore_check.sh > "$WORK/par$i.log" 2>&1 &
    pids+=($!)
done
for i in 0 1 2; do
    wait "${pids[$i]}" || { cat "$WORK/par$((i + 1)).log"; die "동시 실행 $((i + 1))번 실패"; }
done
names="$(grep -ho '→ 임시 DB lemit_rc_[0-9_]*' "$WORK"/par*.log | sort -u | wc -l)"  # 각 실행이 만든 이름만 (참고 목록 제외)
[ "$names" -eq 3 ] || die "동시 실행의 임시 DB 이름이 겹침 ($names개)"
[ "$(temp_dbs)" = "0" ] || die "동시 실행 후 임시 DB가 남음"
[ "$(psql -d lemit_rc_test_existing -c 'SELECT v FROM marker')" = "keep-me" ] || die "동시 실행 중 기존 DB 손상"
ok "동시 3개 실행 모두 통과, 서로 다른 임시 DB, 모두 정리"

# 4) 깨진 백업
head -c 4000 "$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lemit-*.dump' | sort | tail -n 1)" > "$WORK/broken.dump"
if bash scripts/restore_check.sh "$WORK/broken.dump" > "$WORK/broken.log" 2>&1; then die "깨진 백업인데 통과"; fi
[ "$(temp_dbs)" = "0" ] || die "깨진 백업 점검 후 임시 DB가 남음"
ok "깨진 백업은 실패하고 임시 DB 정리"

echo "[test-restore-check] ${pass}/4 통과"
