.PHONY: backup-scheduled restore-drill up down logs test verify prod-up prod-down prod-logs prod-migrate prod-create-admin prod-seed-frameworks prod-rotate-secret backup restore migrate revision migrate-roundtrip fmt create-admin ping-worker purge-staging e2e e2e-down corpus retrieval extraction-eval seed-frameworks mapping-eval relation-eval

up:
	docker compose up -d --build db redis api

down:
	docker compose down

logs:
	docker compose logs -f api worker

test:
	docker compose run --rm -e TEST_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc_test api pytest -v

# 一次跑完全部验证，每条单独报退出码。
#
# check_source_hygiene 拦的是 2026-09-22 真实发生过的一类：注释里按机构名引用
# 语料。那次擦除用一份手写词表，替换、历史重写、事后扫描三道全用同一份，于是
# 漏掉的 6 家被三道一致地放过。所以它第一条规则是**形状**而不是词表。
#
# 为什么不串成一条管道：`命令 | tail` 报的是 tail 的退出码，不是命令的。
# 2026-09-11 就这样同时藏过一次 playwright 失败和 ruff 的 126 条，两个都显示为绿。
#
# ruff 只报数、不判成败：main 上本来就有约 121 条既有告警，做成硬失败这个目标
# 会永远是红的，然后就没人看了。要看的是「有没有比基线多」。
#
# e2e 走 make e2e 而不是直接 npx playwright test：后者跑在开发栈上，每次留下
# 一个 provider 和一个用户（OQ-3）。慢几分钟，换的是跑完开发库一条痕迹不留。
verify:
	@fail=0; \
	echo "── pytest ──"; \
	docker compose run --rm -e TEST_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc_test api pytest -q; \
	  status=$$?; echo "   pytest exit=$$status"; [ $$status -eq 0 ] || fail=1; \
	echo "── ruff（只报数；对比 main 基线约 121 条）──"; \
	echo "   $$(docker compose run --rm api ruff check app tests 2>&1 | grep -E '^Found|^All checks passed' || echo '没拿到 ruff 的结论行')"; \
	echo "── npm run build ──"; \
	( cd frontend && npm run build >/dev/null ); \
	  status=$$?; echo "   build exit=$$status"; [ $$status -eq 0 ] || fail=1; \
	echo "── i18n code 与词条 ──"; \
	python3 scripts/check_i18n_codes.py; \
	  status=$$?; echo "   i18n exit=$$status"; [ $$status -eq 0 ] || fail=1; \
	echo "── 注释卫生（机构名 / 整段英文）──"; \
	python3 scripts/check_source_hygiene.py; \
	  status=$$?; echo "   hygiene exit=$$status"; [ $$status -eq 0 ] || fail=1; \
	echo "── make e2e（隔离栈，跑完自动 down -v）──"; \
	$(MAKE) e2e; \
	  status=$$?; echo "   e2e exit=$$status"; [ $$status -eq 0 ] || fail=1; \
	echo; \
	if [ $$fail -eq 0 ]; then echo "✅ 五条硬检查都过了；ruff 的数字自己对基线"; \
	else echo "❌ 有硬检查没过，往上翻各自的 exit"; fi; \
	exit $$fail

# ── 生产 ─────────────────────────────────────────────────────
# 与开发栈的区别不是参数，是**形态**：前端是 nginx + 构建产物（不是 vite 开发
# 服务器），后端没有 --reload、源码不挂进容器，只有 web 对外暴露端口。
# 首次部署：cp .env.example .env → 填两个密钥、POSTGRES_PASSWORD 和 SITE_ADDRESS/TLS_ISSUER →
#           make prod-up → make prod-migrate → make prod-create-admin ...
# 项目名必须与开发栈分开：同名会让 compose 把两者当成同一套，
# 在开发机上跑 prod-up 会直接顶掉正在用的开发容器。
# 同一台机器上要再起一套（比如验证"从 GitHub 全新部署"是否可行），换个项目名和
# 端口就行，不会碰到既有的那套：
#   PROD_PROJECT=grc-fresh HTTP_PORT=18080 HTTPS_PORT=18443 make prod-up
PROD_PROJECT ?= grc-prod
PROD = COMPOSE_PROJECT_NAME=$(PROD_PROJECT) docker compose -f docker-compose.prod.yml

prod-up:
	$(PROD) up -d --build

prod-down:
	$(PROD) down

prod-logs:
	$(PROD) logs -f api worker web

prod-migrate:
	$(PROD) run --rm api alembic upgrade head

prod-create-admin:
	$(PROD) run --rm api python -m app.cli create-admin "$(email)" "$(name)" "$(password)"

# 导入随仓库自带的两个 NIST 框架。与 seed-frameworks 的唯一区别是打生产栈——
# 上面那条打的是开发栈，在生产上跑等于什么都没做，而且不会报错（最难查的那种）。
#
# 新部署的第一天要跑一次：空库里没有框架，映射、成熟度评估、覆盖度都无从谈起。
#
# **只能跑一次。** 再跑一遍会以 FrameworkImportError（「框架标识已存在」）退出 1
# ——数据不会被写坏，但这条命令不是幂等的，别放进无人值守的部署脚本里当"确保
# 已导入"用。要重导得先换 key 或先把旧框架删掉。
prod-seed-frameworks:
	$(PROD) run --rm api python -m app.cli import-framework \
	  seeds/nist-csf-2.0.csv nist-csf-2.0 "NIST 网络安全框架 2.0" "NIST CSF 2.0" 2.0 nist.gov
	$(PROD) run --rm api python -m app.cli import-framework \
	  seeds/nist-800-53-r5.csv nist-800-53-r5 "NIST SP 800-53 Rev.5" "NIST SP 800-53 Rev.5" 5.1.1 nist.gov

# 换主密钥：先把新值写进 .env 并 make prod-up 重启，再拿**旧**值跑这条。
prod-rotate-secret:
	@test -n "$(old)" || (echo "用法: make prod-rotate-secret old='旧的 APP_SECRET_KEY'"; exit 1)
	$(PROD) run --rm api python -m app.cli rotate-secret "$(old)"

# 备份：数据库 + 制度原文卷。两样缺一不可——只备库，原文没了引用就指向空气；
# 只备原文，人工裁定的结果全丢。恢复前请先 make prod-down。
# KEEP 份之外的旧备份会被删掉——无人值守的定时任务不带轮转，迟早把盘写满，
# 而盘满之后连新备份也写不成，正好在最需要它的时候没有。
KEEP ?= 14

# STAMP 让库和原文两半用同一个时间戳（原先各自取 date，可能差一秒，配不上对）。
ifndef STAMP
STAMP := $(shell date +%Y%m%d-%H%M%S)
endif

backup:
	@mkdir -p backups
	$(PROD) exec -T db pg_dump -U $${POSTGRES_USER:-grc} -d $${POSTGRES_DB:-grc} \
	  > backups/db-$(STAMP).sql
	$(PROD) run --rm --no-deps -v "$(PWD)/backups:/backup" worker \
	  tar czf /backup/docstore-$(STAMP).tar.gz -C /data documents
	@ls -1t backups/db-*.sql        2>/dev/null | tail -n +$$(($(KEEP)+1)) | xargs -r rm --
	@ls -1t backups/docstore-*.tar.gz 2>/dev/null | tail -n +$$(($(KEEP)+1)) | xargs -r rm --
	@ls -1t backups/manifest-*.txt   2>/dev/null | tail -n +$$(($(KEEP)+1)) | xargs -r rm --
	@echo "备份完成，保留最近 $(KEEP) 份："; ls -lh backups | tail -3

# 定时备份入口：make backup + 完整性校验 + 清单 + 异地复制（scripts/backup.sh）
backup-scheduled:
	PROD_PROJECT=$(PROD_PROJECT) scripts/backup.sh

# 在隔离的临时栈里恢复最新（或指定）备份并逐项核对，跑完自动拆除
restore-drill:
	PROD_PROJECT=$(PROD_PROJECT) scripts/restore-drill.sh $(stamp)

restore:
	@test -n "$(db)" || (echo "用法: make restore db=backups/db-....sql docs=backups/docstore-....tar.gz"; exit 1)
	$(PROD) exec -T db psql -U $${POSTGRES_USER:-grc} -d $${POSTGRES_DB:-grc} < "$(db)"
	@test -z "$(docs)" || $(PROD) run --rm -v "$(PWD)/$(dir $(docs)):/backup" worker \
	  tar xzf "/backup/$(notdir $(docs))" -C /data

migrate:
	docker compose run --rm api alembic upgrade head

revision:
	docker compose run --rm api alembic revision --autogenerate -m "$(m)"

# 验收清单要求的迁移往返。downgrade 会真的 DROP TABLE，所以必须打一次性库：
# alembic 只认 ALEMBIC_DATABASE_URL 或 .env 的 DATABASE_URL，传 APP_DATABASE_URL
# 是没用的——那样会静默地在开发库上删表。
migrate-roundtrip:
	docker compose exec -T db psql -U grc -d postgres -c "DROP DATABASE IF EXISTS grc_roundtrip"
	docker compose exec -T db psql -U grc -d postgres -c "CREATE DATABASE grc_roundtrip OWNER grc"
	docker compose run --rm --no-deps \
	  -e ALEMBIC_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc_roundtrip \
	  api sh -c 'alembic upgrade head && alembic downgrade $(to) && alembic upgrade head && alembic check'
	docker compose exec -T db psql -U grc -d postgres -c "DROP DATABASE IF EXISTS grc_roundtrip"

i18n-check:
	python3 scripts/check_i18n_codes.py

fmt:
	docker compose run --rm api ruff check --fix app tests

create-admin:
	docker compose run --rm api python -m app.cli create-admin "$(email)" "$(name)" "$(password)"

ping-worker:
	docker compose run --rm api python -c "import asyncio; from app.worker import enqueue; print(asyncio.run(enqueue('ping')))"

# 暂存卷的定时清理平时由 worker 的 cron 跑；这个入口用来立刻跑一次。
purge-staging:
	docker compose run --rm worker python -m app.cli purge-staging

# 冒烟用例会建 provider、建用户、往暂存卷写文件，而 provider 和用户都没有删除
# 接口（审计留痕要求如此）。所以 e2e 不跑在开发栈上，而是另起一个 compose 项目：
# 项目名不同 → 容器、网络、数据卷（pgdata/docstore/staging）全部另开一套，
# 跑完 down -v 一次性扔掉，开发库一条痕迹都不留（OQ-3）。端口也要错开，
# 否则和正在跑的开发栈抢 5432/6379/8000/5173。
E2E_COMPOSE = COMPOSE_PROJECT_NAME=grc-e2e DB_PORT=5433 REDIS_PORT=6380 API_PORT=8001 WEB_PORT=5174 docker compose

# 先 down -v 再起：上一次被打断、或有人在这套栈上手动跑过单个 spec，库里就留着
# 那次建的数据。有几条用例依赖冷启动状态（比如「还没有证据类型」），会因此失败，
# 而失败看起来像回归（2026-09-23 撞过一次）。
e2e:
	@set -e; \
	trap 'status=$$?; trap - EXIT; $(MAKE) e2e-down || status=1; exit $$status' EXIT; \
	$(E2E_COMPOSE) down -v; \
	$(E2E_COMPOSE) up -d --build; \
	$(E2E_COMPOSE) run --rm api alembic upgrade head; \
	$(E2E_COMPOSE) run --rm api python -m app.cli create-admin admin@example.com Admin pw123456; \
	(cd frontend && npm ci --silent && npx playwright install --with-deps chromium); \
	(cd frontend && E2E_BASE_URL=http://localhost:5174 E2E_API_BASE=http://localhost:8001 npm run test:e2e)

# 单独留一个出口：e2e 中途被 Ctrl-C 打断时，那套栈还在后台占着端口。
e2e-down:
	$(E2E_COMPOSE) down -v

# 语料报告默认跑仓库自带的样本。换一批制度文档时传 CORPUS_DIR——
# 它**只过解析层，不写任何库**，所以可以在导入之前先看条款树切不切得开：
#   make corpus CORPUS_DIR=/Users/me/某机构制度
# 必须是绝对路径（docker 的 bind mount 不认相对路径，也不展开 ~）。
# 另一家机构的文风不同，「Introduction / Roles and Responsibilities」这套锚点
# 会无谓地失败，用 CORPUS_ANCHORS 换一套，或 CORPUS_ANCHORS=none 先跳过：
#   make corpus CORPUS_DIR=/abs/path CORPUS_ANCHORS=none
CORPUS_DIR ?= $(PWD)/sample docs

corpus:
	docker compose run --rm --no-deps \
	  -e TEST_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc_test \
	  $(if $(CORPUS_ANCHORS),-e CORPUS_ANCHORS="$(CORPUS_ANCHORS)",) \
	  -v "$(CORPUS_DIR):/samples:ro" \
	  api pytest tests/test_corpus_report.py -v -s

retrieval:
	docker compose run --rm --no-deps \
	  -e TEST_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc_test \
	  -e RUN_RETRIEVAL_EVAL=1 \
	  -e APP_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc \
	  -v "$(PWD)/sample docs:/samples:ro" \
	  api pytest tests/test_retrieval_quality.py -v -s

extraction-eval:
	docker compose run --rm --no-deps \
	  -e RUN_EXTRACTION_EVAL=1 \
	  -e APP_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc \
	  api pytest tests/test_extraction_quality.py -v -s

seed-frameworks:
	docker compose run --rm api python -m app.cli import-framework \
	  seeds/nist-csf-2.0.csv nist-csf-2.0 "NIST 网络安全框架 2.0" "NIST CSF 2.0" 2.0 nist.gov
	docker compose run --rm api python -m app.cli import-framework \
	  seeds/nist-800-53-r5.csv nist-800-53-r5 "NIST SP 800-53 Rev.5" "NIST SP 800-53 Rev.5" 5.1.1 nist.gov

mapping-eval:
	docker compose run --rm --no-deps \
	  -e RUN_MAPPING_EVAL=1 \
	  -e APP_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc \
	  api pytest tests/test_mapping_quality.py -v -s

relation-eval:
	docker compose run --rm --no-deps \
	  -e RUN_RELATION_EVAL=1 \
	  -e APP_DATABASE_URL=postgresql+asyncpg://grc:grc@db:5432/grc \
	  api pytest tests/test_relation_quality.py -v -s
