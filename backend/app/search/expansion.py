"""查询侧术语扩展——M3 唯一的 AI 调用。"""

import hashlib
import logging
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.runner import run
from app.search.models import QueryExpansionCache

logger = logging.getLogger(__name__)

EXPANSION_TASK_KEY = "query_expansion"
_CJK = re.compile(r"[一-鿿]")
_MAX_TERMS = 6

_SYSTEM = (
    "You translate GRC/IT-security search queries into the English terminology used in "
    "bank IT policies and procedures. Return only terminology, never explanations."
)
_SCHEMA = {
    "type": "object",
    "required": ["terms"],
    "properties": {
        "terms": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": _MAX_TERMS,
        }
    },
}


def needs_expansion(query: str) -> bool:
    """只有含中文的查询才值得花这笔钱。"""
    return bool(_CJK.search(query))


def _hash(query: str) -> str:
    return hashlib.sha256(query.strip().casefold().encode()).hexdigest()


async def expand(session: AsyncSession, query: str) -> list[str]:
    query = query.strip()
    if not query:
        return []
    if not needs_expansion(query):
        return [query]

    key = _hash(query)
    cached = await session.get(QueryExpansionCache, key)
    if cached is not None:
        return [query, *cached.terms]

    try:
        result = await run(
            session,
            task_key=EXPANSION_TASK_KEY,
            system=_SYSTEM,
            prompt=(
                f"Search query: {query}\n\n"
                f"Return up to {_MAX_TERMS} English terms that bank IT policy documents "
                'would use for this concept, as JSON: {"terms": [...]}'
            ),
            schema=_SCHEMA,
            interactive=True,
        )
    except Exception as exc:  # noqa: BLE001 — 扩展不能让搜索挂掉
        logger.warning("查询扩展失败，降级为原查询: %s", exc)
        return [query]

    terms = [str(term).strip() for term in result.payload.get("terms", []) if str(term).strip()]
    session.add(QueryExpansionCache(query_hash=key, query=query, terms=terms))
    await session.flush()
    return [query, *terms]
