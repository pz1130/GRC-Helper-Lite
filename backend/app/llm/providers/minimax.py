"""MiniMax provider。

chat 走 MiniMax 的 OpenAI 兼容接口，直接继承 OpenAICompatProvider 即可。
**embedding 不兼容**，三处不同：

| | OpenAI | MiniMax |
|---|---|---|
| 请求字段 | `input` | `texts` |
| 响应字段 | `data[].embedding` | `vectors` |
| 错误表示 | HTTP 4xx/5xx | **HTTP 200 + base_resp.status_code != 0** |

最后一条最危险：出错也返回 200，只看 HTTP 状态码会让错误一路穿过去，
然后在取 `vectors` 时以 KeyError 崩掉，而不是干净的 ProviderError。

`type` 参数（db / query）是非对称向量的方向，用错会降低检索质量。
"""

from app.llm.providers.base import (
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderError,
    classify,
)
from app.llm.providers.openai_compat import OpenAICompatProvider

# MiniMax 用 base_resp.status_code 表示错误，0 为成功
_OK = 0
# 这些是"稍后重试可能成功"的：限流与内部错误
_RETRYABLE_CODES = frozenset({1002, 1027, 1039, 2013})


class MiniMaxProvider(OpenAICompatProvider):
    async def _post(self, path: str, payload: dict) -> dict:
        body = await super()._post(path, payload)

        # HTTP 200 不代表成功——必须查 base_resp，否则错误会被当成正常响应
        base = body.get("base_resp")
        if isinstance(base, dict) and base.get("status_code", _OK) != _OK:
            code = base.get("status_code")
            raise ProviderError(
                f"MiniMax 返回业务错误 {code}: {base.get('status_msg', '')}",
                retryable=code in _RETRYABLE_CODES,
            )
        return body

    async def embed(self, req: EmbeddingRequest) -> EmbeddingResponse:
        body = await self._post(
            "/embeddings",
            {
                "model": req.model,
                "texts": req.texts,          # 不是 input
                "type": "query" if req.purpose == "query" else "db",
            },
        )

        vectors = body.get("vectors")
        if not isinstance(vectors, list):
            raise ProviderError(
                "MiniMax embedding 响应里没有 vectors 字段", retryable=False
            )
        if len(vectors) != len(req.texts):
            # 顺序与数量都必须对得上，错位是静默的灾难
            raise ProviderError(
                f"MiniMax 返回 {len(vectors)} 个向量，但送入了 {len(req.texts)} 段文本",
                retryable=False,
            )

        usage = body.get("total_tokens") or 0
        return EmbeddingResponse(vectors=vectors, tokens_in=int(usage))


__all__ = ["MiniMaxProvider", "classify"]
