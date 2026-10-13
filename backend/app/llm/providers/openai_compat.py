"""覆盖所有 OpenAI 兼容端点的单一实现。

OpenAI / Azure OpenAI / DeepSeek / 通义千问 / Ollama / 任意自建兼容服务，
它们的差别只在 base_url 和模型名。为每家写一个类是重复代码，
且会让"新增一家厂商"变成改代码而不是改配置。
"""

from urllib.parse import urlsplit, urlunsplit

import httpx

from app.crypto import decrypt
from app.llm.models import LLMProviderConfig
from app.llm.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbeddingRequest,
    EmbeddingResponse,
    ProviderError,
    classify,
)

# 120s 对映射任务不够：framework_mapping 有 26% 的调用超过 110s。超时会触发
# _call_with_retry 的三次重试，每次再等一个完整超时，最后抛 ProviderError——
# 实测一次这样的连锁让 51 分钟、20 个已完成批次全部作废。成功的调用按定义
# 都在旧上限之内，所以真实的单次延迟上界尚未测到。放宽到 300s 后实测
# CSF 批次落在 268–296s，仍紧贴上限，故再放宽到 600s。代价是真正卡住
# 的请求要等更久，且 _call_with_retry 最多重试三次。
_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
_MAX_DETAIL = 300


def _safe_detail(response: httpx.Response) -> str:
    """上游响应体的截断片段。响应体里没有我们的凭据，回显它是安全的。"""
    try:
        return response.text[:_MAX_DETAIL].replace("\n", " ").strip() or "(空响应体)"
    except Exception:  # noqa: BLE001
        return "(响应体不可读)"


class OpenAICompatProvider:
    def __init__(self, config: LLMProviderConfig) -> None:
        from app.llm.providers.factory import DEFAULT_BASE_URLS

        self._config = config
        self._api_key = decrypt(config.api_key_encrypted)
        self._base_url = (config.base_url or DEFAULT_BASE_URLS.get(config.kind, "")).rstrip("/")
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        """把 path 接到 base_url 的**路径**上，保留它自带的查询参数。

        朴素的字符串拼接会得到 `...?GroupId=123/embeddings`——path 跑到了
        查询串后面。这不是某一家的特例：MiniMax 中国站要 ?GroupId=，
        Azure OpenAI 要 ?api-version=，两者都会被拼坏。
        """
        parts = urlsplit(self._base_url)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path.rstrip("/") + path, parts.query, "")
        )

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            response = await self._client.post(
                self._url(path), json=payload, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"网络错误: {type(exc).__name__}", retryable=True) from exc

        if response.status_code >= 400:
            # 回显**响应**体，不回显请求体或请求头——API key 在后两者里。
            # 只报状态码等于把上游给的原因扔掉，问题就没法诊断。
            raise ProviderError(
                f"上游返回 {response.status_code}: {_safe_detail(response)}",
                retryable=classify(response.status_code),
            )
        return response.json()

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        body = await self._post(
            "/chat/completions",
            {
                "model": req.model,
                "temperature": req.temperature,
                "max_tokens": req.max_tokens,
                "messages": [
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": req.prompt},
                ],
            },
        )
        usage = body.get("usage", {})
        return CompletionResponse(
            text=body["choices"][0]["message"]["content"] or "",
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )

    async def embed(self, req: EmbeddingRequest) -> EmbeddingResponse:
        body = await self._post("/embeddings", {"model": req.model, "input": req.texts})
        # 服务端不保证顺序，按 index 归位
        items = sorted(body["data"], key=lambda item: item["index"])
        return EmbeddingResponse(
            vectors=[item["embedding"] for item in items],
            tokens_in=body.get("usage", {}).get("prompt_tokens", 0),
        )
