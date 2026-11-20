#!/usr/bin/env bash
# PostgreSQL 백업 (#44, OPERATIONS §6). 서버의 저장소 폴더에서:  bash scripts/backup_db.sh
#
# - db 컨테이너 안에서 pg_dump(압축 형식 -Fc)로 전체 DB를 백업한다. DB 비밀번호는 쓰지 않는다
#   (컨테이너 안 소켓 연결, 사용자·DB 이름만 .env에서 읽음).
# - 저장 위치: BACKUP_DIR (기본 ~/lemit-backups). 저장소 폴더 밖에 두어 git·이미지에 들어가지 않게 한다.
# - 파일 권한 600, 폴더 700.
# - 만든 직후 **목차 확인**(pg_restore --list): 압축 백업의 형식·목차가 읽히고 테이블 데이터 항목이 있는지만 본다.
#   데이터 블록을 모두 읽거나 복원해 보지는 않으므로 **무결성 검증이 아니다.** 실제로 복원되는지는
#   scripts/restore_check.sh(임시 DB에 전체 복원 + 건수 비교)로 따로 확인한다. 자동 백업은 두 단계를 차례로 실행한다
#   (deploy/systemd/lemit-backup.service, PR #46 리뷰).
# - 보관: 최근 BACKUP_KEEP개(기본 14)만 남기고 오래된 백업 파일을 지운다 (이 스크립트가 만든 lemit-*.dump만).
# - 서버 디스크가 통째로 망가지면 이 백업도 함께 사라진다 → Lightsail 자동 스냅샷을 함께 켠다 (DEPLOY.md §9).
# 종료 코드: 성공 0, 실패 1 (systemd 타이머가 실패를 기록)
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_DIR="${BACKUP_DIR:-$HOME/lemit-backups}"
KEEP="${BACKUP_KEEP:-14}"
COMPOSE=(docker compose)
[[ "$KEEP" =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 1 ] || { echo "[backup] BACKUP_KEEP은 1 이상의 정수"; exit 1; }

get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | tr -d '\r' | sed -e 's/^"//' -e 's/"$//'; }
[ -f .env ] || { echo "[backup] .env가 없습니다"; exit 1; }
DB_USER="$(get POSTGRES_USER)"
DB_NAME="$(get POSTGRES_DB)"
[[ "$DB_USER" =~ ^[A-Za-z0-9_]+$ && "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]] || { echo "[backup] POSTGRES_USER/DB 값 확인"; exit 1; }

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
STAMP="$(date '+%Y%m%d-%H%M%S')"
FILE="$BACKUP_DIR/lemit-$STAMP.dump"
TMP="$FILE.partial"
trap 'rm -f "$TMP"' EXIT

echo "[backup] $DB_NAME → $FILE"
"${COMPOSE[@]}" exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner > "$TMP"
[ -s "$TMP" ] || { echo "[backup] 백업 파일이 비어 있습니다"; exit 1; }

# 목차 확인: 압축 백업의 목차를 읽어 본다. 형식이 깨졌거나 목차가 비면 실패 (데이터 전체 검증은 restore_check.sh)
ENTRIES="$("${COMPOSE[@]}" exec -T db pg_restore --list < "$TMP" | grep -c 'TABLE DATA' || true)"
[ "${ENTRIES:-0}" -gt 0 ] || { echo "[backup] 백업 목차에 테이블 데이터가 없습니다 (손상 의심)"; exit 1; }
mv "$TMP" "$FILE"
chmod 600 "$FILE"
echo "[backup] 완료: $(du -h "$FILE" | cut -f1), 목차 확인 통과(테이블 데이터 항목 ${ENTRIES}개)"
echo "[backup] 복원 검증은 별도 단계: bash scripts/restore_check.sh"

# 보관 개수 정리 (이 스크립트가 만든 파일 이름 형식만, 이름순 = 시간순)
mapfile -t OLD < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lemit-????????-??????.dump' | sort | head -n -"$KEEP")
for f in "${OLD[@]}"; do rm -f -- "$f"; echo "[backup] 오래된 백업 삭제: $(basename "$f")"; done
echo "[backup] 보관 중: $(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'lemit-*.dump' | wc -l)개 (최대 $KEEP)"
