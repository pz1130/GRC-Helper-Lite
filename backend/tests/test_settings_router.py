import pytest

from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _seed(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str) -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_create_provider_never_returns_plaintext_key(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")

    resp = await client.post(
        "/api/settings/providers",
        json={
            "name": "prod-anthropic",
            "kind": "anthropic",
            "model": "claude-opus-5",
            "api_key": "sk-ant-supersecret",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    assert "sk-ant-supersecret" not in resp.text
    assert resp.json()["api_key_masked"] == "…cret"


@pytest.mark.asyncio
async def test_list_providers_masks_keys(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    await client.post(
        "/api/settings/providers",
        json={"name": "p", "kind": "openai", "model": "gpt-4o", "api_key": "sk-abcdefgh1234"},
        headers=headers,
    )
    resp = await client.get("/api/settings/providers", headers=headers)
    assert "sk-abcdefgh1234" not in resp.text


@pytest.mark.asyncio
async def test_grc_lead_cannot_touch_provider_config(client, db_session):
    """spec §8.1：GRC Lead 碰不到 AI 配置。"""
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    assert (await client.get("/api/settings/providers", headers=headers)).status_code == 403


@pytest.mark.asyncio
async def test_provider_change_is_audited_without_the_key(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    await client.post(
        "/api/settings/providers",
        json={"name": "p", "kind": "openai", "model": "gpt-4o", "api_key": "sk-leakcheck"},
        headers=headers,
    )
    log = await client.get("/api/audit-log?entity_type=LLMProviderConfig", headers=headers)
    assert any(e["action"] == "provider.create" for e in log.json())
    assert "sk-leakcheck" not in log.text


@pytest.mark.asyncio
async def test_redaction_preview_shows_what_would_be_sent(client, db_session):
    """spec §6.2：调用前可预览脱敏后实际发送的内容。"""
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    await client.post(
        "/api/settings/redaction",
        json={
            "ruleset": "generation",
            "pattern_type": "regex",
            "pattern": r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
            "replacement_prefix": "IP",
        },
        headers=headers,
    )
    resp = await client.post(
        "/api/settings/redaction/preview",
        json={"ruleset": "generation", "text": "跳板机 10.20.30.40 需复核"},
        headers=headers,
    )
    body = resp.json()
    assert "10.20.30.40" not in body["redacted"]
    assert "[[IP_1]]" in body["redacted"]
    assert body["hits"] == {"IP": 1}


@pytest.mark.asyncio
async def test_thresholds_roundtrip(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    await client.put(
        "/api/settings/thresholds",
        json={
            "auto_accept_threshold": 0.95,
            "force_manual_threshold": 0.5,
            "review_sample_rate": 0.1,
            "monthly_budget_usd": 300,
        },
        headers=headers,
    )
    resp = await client.get("/api/settings/thresholds", headers=headers)
    assert resp.json()["auto_accept_threshold"] == 0.95
    assert resp.json()["review_sample_rate"] == 0.1


@pytest.mark.asyncio
async def test_threshold_ordering_is_enforced(client, db_session):
    """自动接受阈值必须高于强制人工阈值，否则语义矛盾。"""
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    resp = await client.put(
        "/api/settings/thresholds",
        json={
            "auto_accept_threshold": 0.4,
            "force_manual_threshold": 0.8,
            "review_sample_rate": 0.1,
            "monthly_budget_usd": 300,
        },
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_usage_endpoint_readable_by_viewer(client, db_session):
    await _seed(db_session, Role.VIEWER, "v@example.com")
    headers = await _auth(client, "v@example.com")
    resp = await client.get("/api/settings/usage", headers=headers)
    assert resp.status_code == 200
    assert "month_to_date_cost" in resp.json()


# ── 「测试连接」必须按能力自适应 ────────────────────────────────
# 实测事故：embedding provider（embo-01）点测试连接一律报
# 400 unknown model，因为按钮只会打 chat 接口，管理员会误以为 key 坏了。


def _provider_payload(model: str = "embo-01") -> dict:
    return {"name": f"p-{model}", "kind": "minimax", "model": model, "api_key": "sk-x"}


async def _make_provider(client, headers, model: str = "embo-01") -> int:
    resp = await client.post("/api/settings/providers", json=_provider_payload(model), headers=headers)
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_embedding_only_provider_reports_success(client, db_session):
    """chat 打不通但 embedding 打得通，应当报成功并说明是向量模型。"""
    from unittest.mock import AsyncMock, patch

    from app.clauses.models import EMBEDDING_DIM
    from app.llm.providers.base import EmbeddingResponse, ProviderError

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _make_provider(client, headers)

    fake = AsyncMock()
    fake.complete = AsyncMock(side_effect=ProviderError("上游返回 400: unknown model", retryable=False))
    fake.embed = AsyncMock(
        return_value=EmbeddingResponse(vectors=[[0.1] * EMBEDDING_DIM], tokens_in=3)
    )
    with patch("app.llm.router.build_provider", return_value=fake):
        body = (await client.post(f"/api/settings/providers/{config_id}/test", headers=headers)).json()

    assert body["ok"] is True
    assert body["capability"] == "embedding"
    assert body["code"] == "embedding_ok"
    # 维度是数据，不是句子——前端自己组句，中英各一份
    assert body["dimension"] == EMBEDDING_DIM


@pytest.mark.asyncio
async def test_probe_result_carries_a_translatable_code_not_a_chinese_sentence(client, db_session):
    """探测结果必须是 code，不能是拼好的中文句子。

    实测事故：界面切到英文后，这一列仍然冒出"✅ 连接正常（聊天模型）"——
    因为后端把整句中文塞进 message，前端原样贴出来（Providers.tsx）。
    前端的 en/zh 词条是齐的，漏的是后端这一段。
    """
    from unittest.mock import AsyncMock, patch

    from app.llm.providers.base import CompletionResponse

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _make_provider(client, headers, model="MiniMax-M2")

    fake = AsyncMock()
    fake.complete = AsyncMock(return_value=CompletionResponse(text="ok", tokens_in=1, tokens_out=1))
    with patch("app.llm.router.build_provider", return_value=fake):
        body = (await client.post(f"/api/settings/providers/{config_id}/test", headers=headers)).json()

    assert body["code"] == "chat_ok"
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in str(body)), (
        f"响应里不该有中文：{body}"
    )


@pytest.mark.asyncio
async def test_chat_provider_is_not_probed_for_embeddings(client, db_session):
    """chat 打通就该收手，不必再花一次 embedding 的钱。"""
    from unittest.mock import AsyncMock, patch

    from app.llm.providers.base import CompletionResponse

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _make_provider(client, headers, model="MiniMax-M2")

    fake = AsyncMock()
    fake.complete = AsyncMock(return_value=CompletionResponse(text="ok", tokens_in=1, tokens_out=1))
    fake.embed = AsyncMock()
    with patch("app.llm.router.build_provider", return_value=fake):
        body = (await client.post(f"/api/settings/providers/{config_id}/test", headers=headers)).json()

    assert body["capability"] == "chat"
    fake.embed.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_dimension_is_caught_before_it_reaches_the_database(client, db_session):
    """维度对不上要在这里就说清楚，等写库时才炸就太晚了。"""
    from unittest.mock import AsyncMock, patch

    from app.llm.providers.base import EmbeddingResponse, ProviderError

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _make_provider(client, headers, model="text-embedding-3-large")

    fake = AsyncMock()
    fake.complete = AsyncMock(side_effect=ProviderError("400", retryable=False))
    fake.embed = AsyncMock(return_value=EmbeddingResponse(vectors=[[0.1] * 3072], tokens_in=1))
    with patch("app.llm.router.build_provider", return_value=fake):
        body = (await client.post(f"/api/settings/providers/{config_id}/test", headers=headers)).json()

    assert body["ok"] is False
    assert body["code"] == "embedding_dimension_mismatch"
    # 两个数都要给：前端要说清"拿到的"和"要的"分别是多少
    assert body["dimension"] == 3072
    assert body["expected_dimension"] == 1536


@pytest.mark.asyncio
async def test_both_capabilities_failing_reports_both_reasons(client, db_session):
    from unittest.mock import AsyncMock, patch

    from app.llm.providers.base import ProviderError

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _make_provider(client, headers)

    fake = AsyncMock()
    fake.complete = AsyncMock(side_effect=ProviderError("401 invalid api key", retryable=False))
    fake.embed = AsyncMock(side_effect=ProviderError("401 invalid api key", retryable=False))
    with patch("app.llm.router.build_provider", return_value=fake):
        body = (await client.post(f"/api/settings/providers/{config_id}/test", headers=headers)).json()

    assert body["ok"] is False
    assert body["code"] == "unreachable"
    # 上游的报错原文照抄——那是外部系统说的话，翻译它等于篡改证据。
    # 但要按能力分开装，前端才能分行显示，而不是靠"；"切一个长字符串。
    assert body["failures"] == {
        "chat": "401 invalid api key",
        "embedding": "401 invalid api key",
    }


# ── provider 删除 ──────────────────────────────────────────────
# 这张表此前只进不出：每次 e2e 冒烟留一行，开发库攒到 33 条，只能写 SQL 清。
# 它不是审计证据，删掉不会让任何历史记录指向空气——llm_call.provider_config_id
# 是 ON DELETE SET NULL。但**被任务路由指着的不能删**，否则那个任务直接哑掉。


async def _named_provider(client, headers, name: str) -> int:
    """本节要按名字认人，不能复用上面那个按 model 建的 _make_provider。"""
    resp = await client.post(
        "/api/settings/providers",
        json={"name": name, "kind": "openai", "model": "gpt-4o", "api_key": "sk-abcdefgh1234"},
        headers=headers,
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_an_unused_provider_can_be_deleted(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _named_provider(client, headers, "spare")

    resp = await client.delete(f"/api/settings/providers/{config_id}", headers=headers)

    assert resp.status_code == 204
    listed = (await client.get("/api/settings/providers", headers=headers)).json()
    assert [c["id"] for c in listed] == []


@pytest.mark.asyncio
async def test_a_routed_provider_cannot_be_deleted(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _named_provider(client, headers, "bound")
    await client.put(
        "/api/settings/routing",
        json={"task_key": "control_extract", "provider_config_id": config_id},
        headers=headers,
    )

    resp = await client.delete(f"/api/settings/providers/{config_id}", headers=headers)

    assert resp.status_code == 409
    assert "control_extract" in resp.text
    listed = (await client.get("/api/settings/providers", headers=headers)).json()
    assert [c["id"] for c in listed] == [config_id]


@pytest.mark.asyncio
async def test_deleting_a_provider_is_audited_without_the_key(client, db_session):
    from sqlalchemy import select

    from app.iam.models import AuditLog

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    config_id = await _named_provider(client, headers, "gone")

    await client.delete(f"/api/settings/providers/{config_id}", headers=headers)

    entry = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "provider.delete")
    )
    assert entry is not None
    assert entry.before["name"] == "gone"
    assert "sk-abcdefgh1234" not in str(entry.before)
    assert "api_key" not in str(entry.before)


@pytest.mark.asyncio
async def test_deleting_a_missing_provider_is_a_404(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")

    resp = await client.delete("/api/settings/providers/99999", headers=headers)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_grc_lead_cannot_delete_a_provider(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    admin_headers = await _auth(client, "admin@example.com")
    config_id = await _named_provider(client, admin_headers, "spare")
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    lead_headers = await _auth(client, "lead@example.com")

    resp = await client.delete(f"/api/settings/providers/{config_id}", headers=lead_headers)

    assert resp.status_code == 403


# ── 任务注册表与批量绑定 ── OQ-23 ──────────────────────────────────
#
# 11 个推理管道原先只能一条条绑，管理员要连点 11 次下拉。漏配的代价不对等：
# 漏了 embedding，文档解析后向量静默不生成，检索**退化成纯关键字**而界面
# 毫无提示；漏了 conflict_detection，冲突检测干脆不执行。两种都不报错。
#
# 任务清单原先只写在前端（`Providers.tsx` 的 TASK_KEYS），后端散落成各模块的
# 模块常量，于是清单漂移了：注释写"八个推理任务 + embedding"＝9，实际列了 11 个，
# 其中 `maturity_suggestion` 在后端**没有任何消费者**（设计文档 §396 里有，
# 但 maturity 模块是纯计算，没有 prompts）。所以注册表放后端，并带 implemented 标记。


@pytest.mark.asyncio
async def test_task_registry_lists_capability_and_implementation_status(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")

    resp = await client.get("/api/settings/routing/tasks", headers=headers)
    assert resp.status_code == 200
    tasks = {t["key"]: t for t in resp.json()}

    # embedding 要向量模型，其余要聊天模型——这是批量绑定必须分开的原因
    assert tasks["embedding"]["capability"] == "embedding"
    assert tasks["control_extract"]["capability"] == "chat"

    # 设计里有、后端没实现的，如实标出来，不要假装它能用。
    # maturity_suggestion 曾经是唯一一个，现已实现——目前 implemented 全是 True，
    # 所以这里改钉「每个任务都带着这个字段」，别让它退化成没人看的常量。
    assert all("implemented" in spec for spec in tasks.values())
    assert tasks["control_extract"]["implemented"] is True
    assert tasks["maturity_suggestion"]["implemented"] is True


@pytest.mark.asyncio
async def test_bulk_routing_binds_every_implemented_chat_task(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    created = await client.post(
        "/api/settings/providers",
        json={"name": "chat", "kind": "anthropic", "model": "claude-opus-5", "api_key": "sk-x"},
        headers=headers,
    )
    provider_id = created.json()["id"]

    resp = await client.put(
        "/api/settings/routing/bulk",
        json={"provider_config_id": provider_id, "capability": "chat"},
        headers=headers,
    )
    assert resp.status_code == 200

    routing = {r["task_key"]: r["provider_config_id"] for r in
               (await client.get("/api/settings/routing", headers=headers)).json()}
    assert routing["control_extract"] == provider_id
    assert routing["conflict_detection"] == provider_id
    # 向量任务不能被聊天模型顺手绑上——绑错了检索会静默退化
    assert "embedding" not in routing
    # 已实现的聊天任务一个都不能漏——maturity_suggestion 补上实现后也该被绑上
    assert routing["maturity_suggestion"] == provider_id


@pytest.mark.asyncio
async def test_bulk_routing_never_mixes_embedding_with_chat(client, db_session):
    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    emb = (await client.post(
        "/api/settings/providers",
        json={"name": "emb", "kind": "openai", "model": "text-embedding-3-large",
              "api_key": "sk-e"},
        headers=headers,
    )).json()["id"]

    await client.put(
        "/api/settings/routing/bulk",
        json={"provider_config_id": emb, "capability": "embedding"},
        headers=headers,
    )
    routing = {r["task_key"]: r["provider_config_id"] for r in
               (await client.get("/api/settings/routing", headers=headers)).json()}
    assert routing == {"embedding": emb}


@pytest.mark.asyncio
async def test_bulk_routing_overwrites_and_records_one_audit_entry_per_call(client, db_session):
    """批量绑定要留痕，且**按人的动作记一条**而不是按任务记九条。

    管理员眼里这是一次操作；拆成九行会让审计日志变成噪声，真要查
    「谁在什么时候把推理任务切到了哪个 provider」反而更难。明细在 after 里。
    """
    from sqlalchemy import select

    from app.iam.models import AuditLog

    await _seed(db_session, Role.ADMIN, "admin@example.com")
    headers = await _auth(client, "admin@example.com")
    first = (await client.post(
        "/api/settings/providers",
        json={"name": "a", "kind": "anthropic", "model": "claude-opus-5", "api_key": "sk-a"},
        headers=headers,
    )).json()["id"]
    second = (await client.post(
        "/api/settings/providers",
        json={"name": "b", "kind": "anthropic", "model": "claude-sonnet-5", "api_key": "sk-b"},
        headers=headers,
    )).json()["id"]

    await client.put("/api/settings/routing/bulk",
                     json={"provider_config_id": first, "capability": "chat"}, headers=headers)
    await client.put("/api/settings/routing/bulk",
                     json={"provider_config_id": second, "capability": "chat"}, headers=headers)

    routing = {r["task_key"]: r["provider_config_id"] for r in
               (await client.get("/api/settings/routing", headers=headers)).json()}
    assert set(routing.values()) == {second}

    logs = (await db_session.scalars(
        select(AuditLog).where(AuditLog.action == "routing.bulk_upsert")
    )).all()
    assert len(logs) == 2


@pytest.mark.asyncio
async def test_bulk_routing_is_denied_to_non_admin(client, db_session):
    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    resp = await client.put(
        "/api/settings/routing/bulk",
        json={"provider_config_id": 1, "capability": "chat"},
        headers=headers,
    )
    assert resp.status_code == 403


# ── 失败批次在队列里可见且能重跑 ── OQ-22 ──────────────────────────


@pytest.mark.asyncio
async def test_failed_batches_are_listed_separately_from_the_pending_queue(client, db_session):
    """失败行不混进待确认列表，但要有地方看得到，并带上原因与文档。"""
    from app.review.models import Proposal, ProposalKind, ProposalStatus

    await _seed(db_session, Role.GRC_LEAD, "lead@example.com")
    headers = await _auth(client, "lead@example.com")
    db_session.add(
        Proposal(
            kind=ProposalKind.CONTROL_EXTRACT,
            payload={"batch_fingerprint": "abc", "clause_ids": [1, 2]},
            citations=[],
            status=ProposalStatus.FAILED,
            reject_reason="schema: 模型输出中没有找到合法的 JSON 对象",
        )
    )
    await db_session.flush()

    queue = await client.get("/api/proposals", headers=headers)
    assert queue.json() == [], "失败行不是待人决策的提案，不该出现在待确认队列里"

    failures = await client.get("/api/proposals/failures", headers=headers)
    assert failures.status_code == 200
    rows = failures.json()
    assert len(rows) == 1
    assert "JSON" in rows[0]["reject_reason"]
    assert rows[0]["clause_ids"] == [1, 2]


@pytest.mark.asyncio
async def test_failed_batches_do_not_inflate_the_pending_badge(client, db_session):
    from app.review.models import Proposal, ProposalKind, ProposalStatus

    await _seed(db_session, Role.GRC_LEAD, "lead2@example.com")
    headers = await _auth(client, "lead2@example.com")
    db_session.add(
        Proposal(
            kind=ProposalKind.CONTROL_EXTRACT,
            payload={"batch_fingerprint": "x"},
            citations=[],
            status=ProposalStatus.FAILED,
            reject_reason="boom",
        )
    )
    await db_session.flush()

    stats = (await client.get("/api/proposals/stats", headers=headers)).json()
    assert stats["pending"] == 0
    # 但失败数要单独给出来，否则界面无从知道该不该提示
    assert stats["failed"] == 1
