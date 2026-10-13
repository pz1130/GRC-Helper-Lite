import httpx
import pytest

from app.crypto import encrypt
from app.llm.models import LLMProviderConfig, ProviderKind
from app.llm.providers.base import CompletionRequest, EmbeddingRequest, ProviderError
from app.llm.providers.factory import DEFAULT_BASE_URLS, build_provider
from app.llm.providers.minimax import MiniMaxProvider


def _config() -> LLMProviderConfig:
    return LLMProviderConfig(
        id=1, name="minimax", kind=ProviderKind.MINIMAX, model="embo-01",
        base_url="https://api.minimaxi.com/v1", api_key_encrypted=encrypt("sk-mm"),
    )


def _provider(handler) -> MiniMaxProvider:
    provider = MiniMaxProvider(_config())
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def test_factory_routes_minimax_to_its_own_provider():
    assert isinstance(build_provider(_config()), MiniMaxProvider)


def test_cn_base_url_is_the_default():
    assert DEFAULT_BASE_URLS[ProviderKind.MINIMAX] == "https://api.minimaxi.com/v1"


@pytest.mark.asyncio
async def test_embed_uses_texts_not_input():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"vectors": [[0.1, 0.2]], "total_tokens": 7, "base_resp": {"status_code": 0}}
        )

    await _provider(handler).embed(EmbeddingRequest(texts=["a"], model="embo-01"))
    assert "texts" in seen and "input" not in seen


@pytest.mark.asyncio
async def test_embed_reads_vectors_not_data():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"vectors": [[0.1, 0.2], [0.3, 0.4]], "total_tokens": 9,
                  "base_resp": {"status_code": 0}},
        )

    resp = await _provider(handler).embed(EmbeddingRequest(texts=["a", "b"], model="embo-01"))
    assert resp.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert resp.tokens_in == 9


@pytest.mark.asyncio
async def test_document_and_query_use_different_type_directions():
    """非对称向量：建库用 db，检索用 query，用错方向会降低检索质量。"""
    import json

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["type"])
        return httpx.Response(200, json={"vectors": [[0.1]], "base_resp": {"status_code": 0}})

    provider = _provider(handler)
    await provider.embed(EmbeddingRequest(texts=["a"], model="embo-01", purpose="document"))
    await provider.embed(EmbeddingRequest(texts=["a"], model="embo-01", purpose="query"))
    assert seen == ["db", "query"]


@pytest.mark.asyncio
async def test_business_error_inside_http_200_becomes_a_provider_error():
    """MiniMax 出错也返回 200——只看 HTTP 状态码会让错误一路穿过去。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}}
        )

    with pytest.raises(ProviderError) as exc:
        await _provider(handler).embed(EmbeddingRequest(texts=["a"], model="embo-01"))
    assert "1004" in str(exc.value)
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_rate_limit_business_error_is_retryable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}
        )

    with pytest.raises(ProviderError) as exc:
        await _provider(handler).embed(EmbeddingRequest(texts=["a"], model="embo-01"))
    assert exc.value.retryable is True


@pytest.mark.asyncio
async def test_vector_count_mismatch_is_rejected():
    """数量对不上说明错位了，错位是静默的灾难，必须当场失败。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"vectors": [[0.1]], "base_resp": {"status_code": 0}})

    with pytest.raises(ProviderError, match="2 段文本"):
        await _provider(handler).embed(EmbeddingRequest(texts=["a", "b"], model="embo-01"))


@pytest.mark.asyncio
async def test_chat_uses_the_openai_compatible_shape():
    """chat 侧 MiniMax 是 OpenAI 兼容的，沿用父类实现即可。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}],
                  "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                  "base_resp": {"status_code": 0}},
        )

    resp = await _provider(handler).complete(
        CompletionRequest(system="s", prompt="p", model="MiniMax-Text-01")
    )
    assert resp.text == "ok"


@pytest.mark.asyncio
async def test_api_key_never_leaks_in_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"base_resp": {"status_code": 1004, "status_msg": "bad"}})

    with pytest.raises(ProviderError) as exc:
        await _provider(handler).embed(EmbeddingRequest(texts=["a"], model="embo-01"))
    assert "sk-mm" not in str(exc.value)
