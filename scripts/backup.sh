#!/usr/bin/env bash
# 定时备份（KNOWN_ISSUES G2）：make backup → 完整性校验 → 清单 → 异地复制。
#
# 由 deploy/systemd/grc-backup.timer 或 cron 调用，也可手动跑：
#   scripts/backup.sh
# 任何一步失败都以非 0 退出，交给 systemd / cron 告警——**静默失败的备份比没有更糟**。
#
# 配置（读 .env，环境变量可覆盖）：
#   BACKUP_KEEP              本地保留份数（默认 14）
#   BACKUP_OFFSITE_RSYNC     rsync 目标，如 user@host:/srv/grc-backups/
#   BACKUP_OFFSITE_RCLONE    rclone 目标，如 remote:bucket/grc
#   BACKUP_VERIFY_RESTORE=1  额外把 dump 灌进 db 容器里的临时库，确认能完整导入（默认开）
set -euo pipefail

cd "$(dirname "$0")/.."
PROD_PROJECT="${PROD_PROJECT:-grc-prod}"
COMPOSE=(docker compose -p "$PROD_PROJECT" -f docker-compose.prod.yml)

# 只取需要的键，不 source 整个 .env（里面有 JSON 之类 shell 不该解释的值）。
env_get() {
  local key="$1" default="${2:-}"
  if [[ -n "${!key:-}" ]]; then printf '%s' "${!key}"; return; fi
  if [[ -f .env ]]; then
    local line
    line="$(grep -E "^${key}=" .env | tail -n1 || true)"
    if [[ -n "$line" ]]; then printf '%s' "${line#*=}"; return; fi
  fi
  printf '%s' "$default"
}

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

PGUSER_="$(env_get POSTGRES_USER grc)"
PGDB_="$(env_get POSTGRES_DB grc)"
KEEP="$(env_get BACKUP_KEEP 14)"
RSYNC_TARGET="$(env_get BACKUP_OFFSITE_RSYNC)"
RCLONE_TARGET="$(env_get BACKUP_OFFSITE_RCLONE)"
VERIFY_RESTORE="$(env_get BACKUP_VERIFY_RESTORE 1)"
STAMP="$(date +%Y%m%d-%H%M%S)"
DB_FILE="backups/db-${STAMP}.sql"
DOCS_FILE="backups/docstore-${STAMP}.tar.gz"
MANIFEST="backups/manifest-${STAMP}.txt"

# 每张表的行数，写进清单；恢复演练拿它逐表核对。
ROWCOUNT_SQL="select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) c from %I', table_name), false, true, '')))[1]::text from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"

log "开始备份 ${STAMP}（项目 ${PROD_PROJECT}）"
POSTGRES_USER="$PGUSER_" POSTGRES_DB="$PGDB_" \
  make --no-print-directory backup PROD_PROJECT="$PROD_PROJECT" STAMP="$STAMP" KEEP="$KEEP"

# ── 完整性校验 ──
[[ -s "$DB_FILE" ]] || die "数据库备份为空或不存在: $DB_FILE"
tail -n 5 "$DB_FILE" | grep -q "PostgreSQL database dump complete" \
  || die "数据库备份不完整（缺少 pg_dump 结束标记）: $DB_FILE"
gzip -t "$DOCS_FILE" || die "原文归档 gzip 校验失败: $DOCS_FILE"
DOC_COUNT="$(tar tzf "$DOCS_FILE" | grep -vc '/$' || true)"

if [[ "$VERIFY_RESTORE" == "1" ]]; then
  VERIFY_DB="grc_backup_verify"
  log "试导入到临时库 ${VERIFY_DB}"
  "${COMPOSE[@]}" exec -T db psql -q -U "$PGUSER_" -d postgres \
    -c "DROP DATABASE IF EXISTS ${VERIFY_DB}" -c "CREATE DATABASE ${VERIFY_DB}" >/dev/null
  if ! "${COMPOSE[@]}" exec -T db psql -q -v ON_ERROR_STOP=1 -U "$PGUSER_" -d "$VERIFY_DB" \
      < "$DB_FILE" >/dev/null; then
    "${COMPOSE[@]}" exec -T db psql -q -U "$PGUSER_" -d postgres -c "DROP DATABASE IF EXISTS ${VERIFY_DB}" >/dev/null || true
    die "dump 无法完整导入临时库"
  fi
  RESTORED_VERSION="$("${COMPOSE[@]}" exec -T db psql -At -U "$PGUSER_" -d "$VERIFY_DB" \
    -c "select version_num from alembic_version")"
  ROWCOUNTS="$("${COMPOSE[@]}" exec -T db psql -At -F= -U "$PGUSER_" -d "$VERIFY_DB" -c "$ROWCOUNT_SQL")"
  "${COMPOSE[@]}" exec -T db psql -q -U "$PGUSER_" -d postgres -c "DROP DATABASE ${VERIFY_DB}" >/dev/null
fi

# ── 清单：恢复时据此核对 ──
APP_KEY="$(env_get APP_SECRET_KEY)"
{
  echo "stamp=${STAMP}"
  echo "project=${PROD_PROJECT}"
  echo "host=$(hostname)"
  echo "alembic_version=$("${COMPOSE[@]}" exec -T db psql -At -U "$PGUSER_" -d "$PGDB_" -c 'select version_num from alembic_version')"
  [[ -n "${RESTORED_VERSION:-}" ]] && echo "verify_restore_alembic_version=${RESTORED_VERSION}"
  echo "document_files=${DOC_COUNT}"
  # 只记指纹，**不记密钥本身**：恢复者据此确认手里托管的 APP_SECRET_KEY 是对的那把。
  if [[ -n "$APP_KEY" ]]; then
    echo "app_secret_key_sha256_prefix=$(printf '%s' "$APP_KEY" | sha256sum | cut -c1-16)"
  fi
  if [[ -n "${ROWCOUNTS:-}" ]]; then
    echo "# rows"
    printf '%s\n' "$ROWCOUNTS" | sed 's/^/rows./'
  fi
  echo "# sha256"
  (cd backups && sha256sum "db-${STAMP}.sql" "docstore-${STAMP}.tar.gz")
} > "$MANIFEST"
log "清单: $MANIFEST"

# ── 异地复制 ──
if [[ -n "$RSYNC_TARGET" ]]; then
  log "rsync → ${RSYNC_TARGET}"
  rsync -a --partial "$DB_FILE" "$DOCS_FILE" "$MANIFEST" "$RSYNC_TARGET"
elif [[ -n "$RCLONE_TARGET" ]]; then
  log "rclone → ${RCLONE_TARGET}"
  rclone copy --checksum backups/ "$RCLONE_TARGET" \
    --include "db-${STAMP}.sql" --include "docstore-${STAMP}.tar.gz" --include "manifest-${STAMP}.txt"
else
  log "WARNING: 未配置 BACKUP_OFFSITE_RSYNC / BACKUP_OFFSITE_RCLONE，备份只在本机，磁盘坏了一起丢"
fi

log "备份完成 ${STAMP}: db $(du -h "$DB_FILE" | cut -f1), 原文 ${DOC_COUNT} 个文件"
