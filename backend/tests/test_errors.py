"""业务错误必须带一个**可翻译的** code。

`message` 是写死的中文句子，前端原样显示——英文界面上就是中文。`code` 原先
只有 6 个通用 HTTP 类别（`not_found` / `conflict` / …），40 多种"某某不存在"
共用一个 `not_found`，所以前端**想翻也翻不了**。

正确范式仓库里已经有一处：provider 测试连接返 `chat_ok` /
`embedding_dimension_mismatch`，前端按 `settings.providersConfig.probe.<code>`
翻译（提交 9b369ee）。这里把同一件事推广到所有业务错误。

迁移是增量的：没带具体 code 的仍然回落到 HTTP 类别，前端 `defaultValue`
兜住 `message`，不会退化成 "errors.undefined"。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import AppError, Conflict, NotFound, install_error_handlers


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/specific")
    def _specific():
        raise NotFound("审计项目不存在", code="audit.engagement_not_found")

    @app.get("/legacy")
    def _legacy():
        raise Conflict("还没迁移的那些")

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_with_params() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/interpolated")
    def _interpolated():
        raise Conflict(
            "还有 3 条证据在用这个类型",
            code="evidence_type.in_use",
            params={"count": 3},
        )

    return TestClient(app, raise_server_exceptions=False)


def test_a_specific_code_reaches_the_client(client):
    """前端要按这个 code 去查词条，所以它必须原样出现在响应里。"""
    response = client.get("/specific")
    assert response.status_code == 404
    assert response.json()["code"] == "audit.engagement_not_found"


def test_the_chinese_sentence_is_still_sent_as_the_fallback(client):
    """迁移期间前端用它兜底，所以不能因为有了 code 就不发 message。"""
    assert client.get("/specific").json()["message"] == "审计项目不存在"


def test_an_unmigrated_error_still_falls_back_to_the_http_class(client):
    """没带 code 的一处都不能坏——这是增量迁移的前提。"""
    response = client.get("/legacy")
    assert response.status_code == 409
    assert response.json() == {"code": "conflict", "message": "还没迁移的那些"}


def test_the_code_is_a_dotted_namespace_not_a_sentence():
    """code 是查词条的键，必须是稳定的 ASCII 标识符，不是给人读的句子。"""
    error = AppError("随便什么话", code="audit.engagement_not_found")
    assert error.code.replace(".", "").replace("_", "").isalnum()
    assert error.code.isascii()


# ── 带变量的错误 ──────────────────────────────────────────────
# "还有 3 条证据在用这个类型" —— 句子里有个数字。光有 code 翻不了这种：
# 前端拿到 `evidence_type.in_use` 也不知道是几条。变量要跟着 code 一起过来，
# 由前端的词条去排版（i18next 的 {{count}}）。


def test_params_travel_with_the_code(client_with_params):
    body = client_with_params.get("/interpolated").json()
    assert body["code"] == "evidence_type.in_use"
    assert body["params"] == {"count": 3}


def test_the_chinese_sentence_still_carries_the_value_for_the_fallback(client_with_params):
    """没迁移时显示的是后端这句话，它必须自己就把数字填好。"""
    assert client_with_params.get("/interpolated").json()["message"] == "还有 3 条证据在用这个类型"


def test_an_error_without_params_does_not_grow_a_params_field(client):
    """绝大多数错误没有变量，响应体不该凭空多一个 null 字段。"""
    assert "params" not in client.get("/specific").json()


def test_validation_summaries_are_one_readable_line():
    from pydantic import BaseModel, ValidationError

    from app.errors import describe_validation

    class Payload(BaseModel):
        title: str
        confidence: float

    try:
        Payload.model_validate({"confidence": "high"})
    except ValidationError as exc:
        summary = describe_validation(exc.errors())
    assert summary == "title: Field required; confidence: Input should be a valid number, unable to parse string as a number"
    assert "input_value" not in summary and "https://" not in summary
