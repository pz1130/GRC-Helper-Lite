#!/usr/bin/env bash
# RCSA 的备份恢复核对（发布前跑一次，换了迁移或冻结规则后再跑）。
#
#   make rcsa-restore-check
#
# 在开发栈的 db 容器里建两个临时库：源库迁到 head，用合成数据走完一期 RCSA 到关闭；
# 用 pg_dump 备份、psql 恢复进另一个库（与 make backup / make restore 同一套工具），
# 再比对两边的指纹——评分版本、响应版本、审计与幂等键、触发关联、登记册关联、闭期报告、
# 冻结触发器——最后确认恢复出来的库仍然拒绝改动已关闭的活动。跑完删掉两个库。
#
# 不碰开发库和生产栈；整份恢复演练（含原文卷与 HTTPS）仍是 scripts/restore-drill.sh。
set -euo pipefail
cd "$(dirname "$0")/.."

SRC=grc_rcsa_drill_src
DST=grc_rcsa_drill_dst
WORK="$(mktemp -d)"
PG=(docker compose exec -T db psql -v ON_ERROR_STOP=1 -U grc)
url() { printf 'postgresql+asyncpg://grc:grc@db:5432/%s' "$1"; }
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "FAIL: $*" >&2; exit 1; }

drop_databases() {
  for db in "$SRC" "$DST"; do
    "${PG[@]}" -d postgres -q -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE)" >/dev/null 2>&1 || true
  done
}
trap 'drop_databases; rm -rf "$WORK"' EXIT

docker compose up -d --wait db >/dev/null
# 上次被打断留下的临时库先清掉。
drop_databases

log "1/5 源库迁到 head 并走完一期 RCSA"
"${PG[@]}" -d postgres -q -c "CREATE DATABASE \"$SRC\" OWNER grc"
docker compose run --rm --no-deps -e ALEMBIC_DATABASE_URL="$(url $SRC)" api alembic upgrade head >/dev/null 2>&1 \
  || die "迁移失败"
docker compose run --rm --no-deps -e DATABASE_URL="$(url $SRC)" api python -m tests.rcsa_drill build 2>/dev/null \
  | tail -n1

log "2/5 源库指纹"
docker compose run --rm --no-deps -e DATABASE_URL="$(url $SRC)" api python -m tests.rcsa_drill fingerprint \
  2>/dev/null >"$WORK/src.json"

log "3/5 pg_dump 备份，psql 恢复进另一个库"
docker compose exec -T db pg_dump -U grc -d "$SRC" >"$WORK/dump.sql"
"${PG[@]}" -d postgres -q -c "CREATE DATABASE \"$DST\" OWNER grc"
"${PG[@]}" -d "$DST" -q <"$WORK/dump.sql" >/dev/null || die "恢复失败"

log "4/5 恢复库指纹并比对"
docker compose run --rm --no-deps -e DATABASE_URL="$(url $DST)" api python -m tests.rcsa_drill fingerprint \
  2>/dev/null >"$WORK/dst.json"
diff -u "$WORK/src.json" "$WORK/dst.json" || die "恢复前后不一致（上面是差异）"
log "  $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum(d["rows"].values()), "RCSA rows,", len(d["audit"]), "audit events,", len(d["db_triggers"]), "freeze triggers")' "$WORK/src.json") 一致"

log "5/5 恢复库仍拒绝改动已关闭的活动"
if "${PG[@]}" -d "$DST" -q -c "UPDATE rcsa_campaigns SET name = 'rewritten' WHERE status = 'closed'" >/dev/null 2>&1; then
  die "恢复后的库允许改动已关闭活动——冻结触发器没有随备份恢复"
fi

log "通过：RCSA 数据经 pg_dump/psql 备份恢复后完整一致，冻结规则仍然生效"
