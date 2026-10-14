#!/usr/bin/env bash
# 恢复演练（KNOWN_ISSUES G2/G3）：把一份备份恢复进**隔离的临时栈**并逐项核对，跑完拆除。
#
#   scripts/restore-drill.sh                  # 最新一份
#   scripts/restore-drill.sh 20261013-201500  # 指定时间戳
#   KEEP_DRILL=1 scripts/restore-drill.sh     # 跑完不拆，留着人工点一遍
#
# 也是**换机恢复**的同一套步骤：在新机器上 clone 仓库、放好 .env（含托管的
# APP_SECRET_KEY）和 backups/ 后运行它，确认无误再按 OPERATIONS.md 正式恢复。
#
# 核对项：清单 sha256 → APP_SECRET_KEY 指纹 → 迁移版本 → 每表行数 → 原文文件数 →
#         全栈起来后经 HTTPS 访问 /api/health。
set -euo pipefail
cd "$(dirname "$0")/.."

DRILL_PROJECT="${DRILL_PROJECT:-grc-drill}"
export HTTP_PORT="${DRILL_HTTP_PORT:-18080}" HTTPS_PORT="${DRILL_HTTPS_PORT:-18443}"
export PROXY_BIND_ADDRESS=127.0.0.1 SITE_ADDRESS=localhost TLS_ISSUER=internal
COMPOSE=(docker compose -p "$DRILL_PROJECT" -f docker-compose.prod.yml)

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "FAIL: $*" >&2; exit 1; }
env_get() {
  local key="$1" default="${2:-}" line
  if [[ -n "${!key:-}" ]]; then printf '%s' "${!key}"; return; fi
  line="$(grep -E "^${key}=" .env 2>/dev/null | tail -n1 || true)"
  [[ -n "$line" ]] && printf '%s' "${line#*=}" || printf '%s' "$default"
}
PGUSER_="$(env_get POSTGRES_USER grc)"
PGDB_="$(env_get POSTGRES_DB grc)"

STAMP="${1:-}"
if [[ -z "$STAMP" ]]; then
  latest="$(ls -1t backups/manifest-*.txt 2>/dev/null | head -n1 || true)"
  [[ -n "$latest" ]] || die "backups/ 里没有 manifest-*.txt（先跑 scripts/backup.sh）"
  STAMP="$(basename "$latest" .txt)"; STAMP="${STAMP#manifest-}"
fi
MANIFEST="backups/manifest-${STAMP}.txt"
DB_FILE="backups/db-${STAMP}.sql"
DOCS_FILE="backups/docstore-${STAMP}.tar.gz"
[[ -f "$MANIFEST" && -f "$DB_FILE" && -f "$DOCS_FILE" ]] || die "备份 ${STAMP} 不全"
man() { grep -E "^$1=" "$MANIFEST" | head -n1 | cut -d= -f2-; }

if [[ "$DRILL_PROJECT" == "${PROD_PROJECT:-grc-prod}" ]]; then
  die "DRILL_PROJECT 不能等于生产项目名，否则会恢复进生产库"
fi

cleanup() {
  if [[ "${KEEP_DRILL:-0}" == "1" ]]; then
    log "KEEP_DRILL=1：临时栈保留，https://localhost:${HTTPS_PORT}/ ；拆除: ${COMPOSE[*]} down -v"
  else
    log "拆除临时栈 ${DRILL_PROJECT}"
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

log "1/6 校验 sha256"
(cd backups && sed -n '/^# sha256$/,$p' "manifest-${STAMP}.txt" | tail -n +2 | sha256sum -c --quiet) \
  || die "sha256 不符，备份文件被改动或损坏"

log "2/6 核对 APP_SECRET_KEY 指纹"
want="$(man app_secret_key_sha256_prefix)"
have="$(printf '%s' "$(env_get APP_SECRET_KEY)" | sha256sum | cut -c1-16)"
if [[ -z "$want" ]]; then
  log "  清单里没有指纹，跳过"
elif [[ "$want" != "$have" ]]; then
  die "当前 .env 的 APP_SECRET_KEY 与备份时不是同一把（$have ≠ $want）。恢复后所有 AI provider 密钥都无法解密——去密钥托管处取回备份时那把"
else
  log "  一致 ($have)"
fi

log "3/6 起空的临时栈并恢复"
"${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
"${COMPOSE[@]}" up -d --wait db redis
POSTGRES_USER="$PGUSER_" POSTGRES_DB="$PGDB_" \
  make --no-print-directory restore PROD_PROJECT="$DRILL_PROJECT" db="$DB_FILE" docs="$DOCS_FILE" >/dev/null

log "4/6 核对迁移版本与每表行数"
got_ver="$("${COMPOSE[@]}" exec -T db psql -At -U "$PGUSER_" -d "$PGDB_" -c 'select version_num from alembic_version')"
[[ "$got_ver" == "$(man alembic_version)" ]] || die "迁移版本 $got_ver ≠ 清单 $(man alembic_version)"
log "  迁移版本 $got_ver"
ROWCOUNT_SQL="select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) c from %I', table_name), false, true, '')))[1]::text from information_schema.tables where table_schema='public' and table_type='BASE TABLE' order by 1"
diff <(grep '^rows\.' "$MANIFEST" | sed 's/^rows\.//') \
     <("${COMPOSE[@]}" exec -T db psql -At -F= -U "$PGUSER_" -d "$PGDB_" -c "$ROWCOUNT_SQL") \
  || die "行数与清单不符（上面是差异）"
log "  $(grep -c '^rows\.' "$MANIFEST") 张表行数一致"

log "5/6 核对原文文件"
got_docs="$("${COMPOSE[@]}" run --rm --no-deps -T worker sh -c 'find /data/documents -type f | wc -l' | tr -d '[:space:]')"
[[ "$got_docs" == "$(man document_files)" ]] || die "原文文件数 $got_docs ≠ 清单 $(man document_files)"
log "  原文 $got_docs 个文件"

log "6/6 起全栈，经 HTTPS 访问健康检查"
"${COMPOSE[@]}" up -d --wait
ok=0
for _ in $(seq 1 30); do
  if curl -fsSk "https://localhost:${HTTPS_PORT}/api/health" >/dev/null; then ok=1; break; fi
  sleep 2
done
[[ $ok == 1 ]] || die "https://localhost:${HTTPS_PORT}/api/health 不通"
curl -sSk -o /dev/null -D - "https://localhost:${HTTPS_PORT}/" | grep -i '^strict-transport-security' >/dev/null \
  || die "HTTPS 响应缺少 HSTS 头"
log "  健康检查通过，HSTS 头存在"

log "演练通过：备份 ${STAMP} 可在本机完整恢复"
