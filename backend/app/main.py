import logging
from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.models
from app.config import get_settings
from app.db import session_factory

logger = logging.getLogger(__name__)



async def database_reachable() -> bool:
    """一次最便宜的往返。连不上、超时、认证失败——都算不健康。"""
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 —— 任何原因连不上都是不健康
        logger.warning("健康检查：数据库不可达")
        return False


@cache
def code_head() -> str | None:
    """这份代码期望的迁移版本。读的是镜像里的迁移脚本，不连库。"""
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


async def schema_at_head(session: AsyncSession | None = None) -> bool:
    """库的迁移版本是否正好是代码的 head（G3）。

    升级顺序是「先备份，再升级，再迁移」：新代码起来到迁移跑完之间连的是旧 schema，
    请求会以奇怪的方式失败。库比代码新（回滚了代码没回滚库）同样算不一致。
    """
    async def read(s: AsyncSession) -> str | None:
        return await s.scalar(text("SELECT version_num FROM alembic_version"))

    try:
        if session is not None:
            revision = await read(session)
        else:
            async with session_factory() as fresh:
                revision = await read(fresh)
    except Exception:  # noqa: BLE001 —— 读不到版本表也是不一致
        logger.warning("健康检查：读不到迁移版本")
        return False
    if revision != code_head():
        logger.warning("健康检查：库的迁移版本 %s ≠ 代码 %s，先跑迁移", revision, code_head())
        return False
    return True


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="GRC Helper", version=settings.app_version)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/api/health")
    async def health(response: Response) -> dict[str, str]:
        """真的查一下库——只返回常量的健康检查只证明进程还活着。

        Redis 不算：限流在它不可用时放行（iam/throttle.py），API 照样能服务，
        把它算进来会让一次缓存抖动变成整个服务被重启。
        """
        reachable = await database_reachable()
        # 连不上库时版本无从谈起，不再多查一次
        current = reachable and await schema_at_head()
        healthy = reachable and current
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if healthy else "degraded",
            "database": "ok" if reachable else "down",
            "schema": "ok" if current else ("mismatch" if reachable else "unknown"),
            "version": settings.app_version,
        }

    from app.audit.router import router as audit_assistant_router
    from app.clauses.router import router as clauses_router
    from app.conflicts.router import router as conflicts_router
    from app.controls.router import router as controls_router
    from app.decision.router import router as decision_router
    from app.environment.router import router as environment_router
    from app.errors import install_error_handlers
    from app.evidence.router import router as evidence_router
    from app.extraction.router import router as extraction_router
    from app.frameworks.router import router as frameworks_router
    from app.graph.router import router as graph_router
    from app.iam.router import audit_router, users_router
    from app.iam.router import router as iam_router
    from app.impact.router import router as impact_router
    from app.indexing.router import router as index_router
    from app.ingest.router import router as documents_router
    from app.llm.router import router as settings_router
    from app.mapping.router import router as mapping_router
    from app.matrix.router import router as matrix_router
    from app.maturity.router import router as maturity_router
    from app.packaging.router import router as packaging_router
    from app.relations.router import router as relations_router
    from app.review.router import router as review_router
    from app.risk.router import router as risk_router
    from app.search.router import router as search_router

    install_error_handlers(application)
    application.include_router(decision_router)
    application.include_router(iam_router)
    application.include_router(audit_assistant_router)
    application.include_router(users_router)
    application.include_router(audit_router)
    application.include_router(settings_router)
    application.include_router(documents_router)
    # 与 ingest 同 prefix /api/documents；排在其后，避免盖住上传与
    # GET /{document_id} 等既有路由（Starlette 按注册顺序匹配同形路径）。
    application.include_router(impact_router)
    application.include_router(clauses_router)
    application.include_router(search_router)
    application.include_router(index_router)
    application.include_router(controls_router)
    application.include_router(extraction_router)
    application.include_router(matrix_router)
    application.include_router(review_router)
    application.include_router(risk_router)
    application.include_router(frameworks_router)
    # 与 frameworks 同 prefix /api/frameworks；按任务要求排在其后。
    application.include_router(packaging_router)
    application.include_router(graph_router)
    application.include_router(mapping_router)
    application.include_router(maturity_router)
    application.include_router(relations_router)
    application.include_router(conflicts_router)
    application.include_router(environment_router)
    application.include_router(evidence_router)

    return application


app = create_app()
