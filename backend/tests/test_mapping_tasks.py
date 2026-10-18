"""纯编排测试：不连数据库，也不用共享 fixture。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.errors import AppError, NotFound
from app.frameworks.models import Framework
from app.llm.runner import ValidatedResult
from app.llm.validation import ValidationFailure, validate
from app.mapping import tasks
from app.mapping.prompts import MAPPING_SCHEMA

ITEM_TEXT = "Identities and credentials are managed for authorized devices."


def mapping(**overrides):
    return {
        "framework_item_id": 2, "control_id": 5, "strength": "partial",
        "framework_item_quote": "Identities and credentials are managed",
        "rationale": "covers identity management", "confidence": 0.85,
        **overrides,
    }


@pytest.fixture
def harness(monkeypatch):
    framework = SimpleNamespace(id=3, key="csf")
    call = SimpleNamespace(id=10, redaction_hits={"dictionary": 2})
    controls = [SimpleNamespace(
        id=5, code="C-0005", title="Identity management",
        statement="All identities are managed centrally.")]
    # 替身要带齐 FrameworkItem 的字段：分批靠 parent_id / attributes 判定要求项，
    # PR 是容器（有子节点），只作上下文，不作映射目标。
    items = [
        SimpleNamespace(id=1, code="PR", title="Protect", description="", level=1,
                        parent_id=None, attributes=None),
        SimpleNamespace(id=2, code="PR.AA-01", title="Identities", description=ITEM_TEXT,
                        level=2, parent_id=1, attributes=None),
    ]

    session = MagicMock()
    session.get = AsyncMock(
        side_effect=lambda model, key: framework if model is Framework else call)
    session.scalars = AsyncMock(side_effect=[controls, items])
    session.scalar = AsyncMock(return_value=None)
    session.execute = AsyncMock()
    # 假会话答不了路由查询；检查点指纹里的模型名固定下来。
    monkeypatch.setattr(tasks, "routed_model", AsyncMock(return_value="model-a"))
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    @asynccontextmanager
    async def savepoint():
        yield None

    session.begin_nested = savepoint

    payload = {"mappings": [mapping()]}

    async def runner(session, **kwargs):
        # 引用校验已移出 run()，runner 只负责 schema。
        validate(payload, kwargs["schema"])
        return ValidatedResult(payload, 0.9, 10)

    run = AsyncMock(side_effect=runner)
    create = AsyncMock(side_effect=lambda *a, **k: SimpleNamespace(id=21))
    monkeypatch.setattr(tasks, "run", run)
    monkeypatch.setattr(tasks.review_service, "create", create, raising=False)
    monkeypatch.setattr(
        tasks.MappingCitationValidator, "check", AsyncMock(return_value=None), raising=False)

    return SimpleNamespace(session=session, framework=framework, call=call,
                           controls=controls, items=items, run=run,
                           create=create, payload=payload)


async def test_a_valid_mapping_becomes_a_proposal_with_a_checkpoint(harness):
    summary = await tasks.run_mapping(harness.session, 3, run_key="t1")

    assert summary["proposals"] == 1
    assert summary["proposal_ids"] == [21]
    assert summary["rejected"] == 0
    kind = harness.create.await_args.kwargs["kind"]
    assert kind.value == "mapping"
    assert harness.create.await_args.kwargs["confidence"] == 0.85
    assert harness.call.redaction_hits[tasks.CHECKPOINT_KEY]["proposal_ids"] == [21]
    assert harness.call.redaction_hits["dictionary"] == 2


async def test_the_prompt_carries_both_the_batch_and_every_control(harness):
    await tasks.run_mapping(harness.session, 3)
    prompt = harness.run.await_args.kwargs["prompt"]
    assert "[framework_item_id=2]" in prompt
    assert "[control_id=5]" in prompt
    assert "All identities are managed centrally." in prompt


async def test_abstention_produces_no_proposal_and_is_not_an_error(harness, monkeypatch):
    async def runner(session, **kwargs):
        return ValidatedResult({"mappings": [], "insufficient_evidence": True}, None, 10)

    monkeypatch.setattr(tasks, "run", AsyncMock(side_effect=runner))
    summary = await tasks.run_mapping(harness.session, 3)

    assert summary["proposals"] == 0
    assert summary["rejected"] == 0
    harness.create.assert_not_awaited()


async def test_a_rejected_batch_is_counted_not_silently_dropped(harness, monkeypatch):
    monkeypatch.setattr(
        tasks, "run", AsyncMock(side_effect=ValidationFailure("引文在框架项中找不到")))
    summary = await tasks.run_mapping(harness.session, 3)

    assert summary["rejected"] == 1
    assert summary["proposals"] == 0
    harness.session.commit.assert_awaited()


async def test_an_unknown_framework_raises(harness):
    harness.session.get = AsyncMock(return_value=None)
    with pytest.raises(NotFound):
        await tasks.run_mapping(harness.session, 999999)


async def test_a_framework_with_no_controls_refuses_to_run(harness):
    harness.session.scalars = AsyncMock(side_effect=[[], []])
    with pytest.raises(AppError):
        await tasks.run_mapping(harness.session, 3)
    harness.run.assert_not_awaited()


async def test_a_large_control_library_is_prefiltered_instead_of_failing(harness, monkeypatch):
    """K16：库超过原先的 60000 字符上限也照常映射，提示词只带 TOP_K 个候选。"""
    huge = [
        # 与框架项相关的要多于 TOP_K 个：纯关键词排序只返回得分大于 0 的控制点
        SimpleNamespace(id=n, code=f"C-{n:04d}", title="Identity management" if n % 3 == 0 else "Backup",
                        statement=("Identities are managed. " if n % 3 == 0 else "Backups run. ") + "x" * 1000)
        for n in range(1, 200)
    ]
    monkeypatch.setattr(tasks, "load_vectors", AsyncMock(return_value=({}, {})))
    harness.session.scalars = AsyncMock(side_effect=[huge, harness.items])
    summary = await tasks.run_mapping(harness.session, 3)
    assert summary["batches"] == 1
    prompt = harness.run.await_args.kwargs["prompt"]
    assert prompt.count("[control_id=") == tasks.TOP_K
    assert "[control_id=3]" in prompt and "[control_id=1]" not in prompt


async def test_a_cached_checkpoint_skips_the_batch(harness):
    harness.session.scalar = AsyncMock(return_value=SimpleNamespace(
        id=10, redaction_hits={tasks.CHECKPOINT_KEY: {"fingerprint": "x",
                                                      "proposal_ids": [99]}}))
    summary = await tasks.run_mapping(harness.session, 3, run_key="t2")

    assert summary["skipped_batches"] == 1
    assert summary["resumed_proposal_ids"] == [99]
    harness.run.assert_not_awaited()


def test_the_schema_forbids_a_mapping_without_a_quote():
    with pytest.raises(ValidationFailure):
        validate({"mappings": [{k: v for k, v in mapping().items() if k != "framework_item_quote"}]},
                 MAPPING_SCHEMA)


async def test_a_provider_error_fails_the_batch_without_killing_the_run(harness, monkeypatch):
    """一次网络超时不该清零整轮。

    实测：51 分钟跑到 20/40 批时一个 ReadTimeout 让整个任务终止，
    已完成的 11 批全部白费——按批检查点在标定路径上帮不上忙，因为
    每次运行都用新的 run_key。
    """
    from app.llm.providers.base import ProviderError

    calls = {"n": 0}

    async def flaky(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("网络错误: ReadTimeout", retryable=True)
        return ValidatedResult({"mappings": [mapping()]}, 0.9, 10)

    monkeypatch.setattr(tasks, "run", AsyncMock(side_effect=flaky))
    harness.items.append(SimpleNamespace(
        id=3, code="DE", title="Detect", description="", level=1,
        parent_id=None, attributes=None))
    harness.items.append(SimpleNamespace(
        id=4, code="DE.CM-01", title="Monitoring", description=ITEM_TEXT,
        level=2, parent_id=3, attributes=None))
    harness.session.scalars = AsyncMock(side_effect=[harness.controls, harness.items])

    summary = await tasks.run_mapping(harness.session, 3)

    assert summary["failed"] == 1          # провайдер 故障单独计数
    assert summary["rejected"] == 0        # 不是质量问题，不该污染通过率
    assert summary["completed_batches"] == 1
    assert calls["n"] == 2                 # 第一批失败后继续跑了第二批


def test_the_schema_caps_how_many_mappings_one_response_may_carry():
    """输出越长模型越容易丢字段、把 JSON 写断——给它一个明确的收口。"""
    assert MAPPING_SCHEMA["properties"]["mappings"]["maxItems"] == 25
    too_many = {"mappings": [mapping() for _ in range(26)]}
    with pytest.raises(ValidationFailure):
        validate(too_many, MAPPING_SCHEMA)
    assert validate({"mappings": [mapping() for _ in range(25)]}, MAPPING_SCHEMA)


async def test_one_bad_citation_drops_only_that_mapping(harness, monkeypatch):
    """一条不合格不该让整批作废。

    实测 CSF 探路：7 批里 2 批因为模型的零星手误（编造 control_id、改写引文）
    整批被丢，而成功批次平均产 20 条映射——两次手误的代价是约 40 条本可用的映射。
    """
    good, bad = mapping(), mapping(framework_item_id=2, control_id=999)
    monkeypatch.setattr(
        tasks, "run",
        AsyncMock(return_value=ValidatedResult({"mappings": [good, bad]}, 0.9, 10)))

    async def check(self, payload):
        # 整批校验只要有一条不合格就报错，逐条校验才分辨得出是哪条。
        bad = [e for e in payload["mappings"] if e["control_id"] != 5]
        return "控制点 999 在库中不存在" if bad else None

    monkeypatch.setattr(tasks.MappingCitationValidator, "check", check)
    summary = await tasks.run_mapping(harness.session, 3)

    assert summary["proposals"] == 1          # 好的留下
    assert summary["dropped_mappings"] == 1   # 坏的单独计数
    assert summary["rejected"] == 0           # 批次没被判死
    assert summary["completed_batches"] == 1


async def test_a_batch_whose_mappings_are_all_bad_survives(harness, monkeypatch):
    monkeypatch.setattr(
        tasks, "run",
        AsyncMock(return_value=ValidatedResult({"mappings": [mapping()]}, 0.9, 10)))
    monkeypatch.setattr(
        tasks.MappingCitationValidator, "check", AsyncMock(return_value="引文找不到"))
    summary = await tasks.run_mapping(harness.session, 3)

    assert summary["proposals"] == 0
    assert summary["dropped_mappings"] == 1
    assert summary["rejected"] == 0
    harness.create.assert_not_awaited()


async def test_abstention_records_no_drops(harness, monkeypatch):
    monkeypatch.setattr(
        tasks, "run",
        AsyncMock(return_value=ValidatedResult(
            {"mappings": [], "insufficient_evidence": True}, None, 10)))
    summary = await tasks.run_mapping(harness.session, 3)
    assert summary["proposals"] == 0 and summary["dropped_mappings"] == 0


async def test_no_transaction_is_held_across_the_model_call(harness, monkeypatch):
    """advisory 锁与事务不得跨越 LLM 调用。

    实测：一次挂起的 HTTP 调用把事务开了 100 分钟，pg_stat_activity 显示
    `idle in transaction / ClientRead`，advisory 锁一并攥住不放。检查点要
    保护的是「查一下有没有做过」，那一步查完就该提交。
    """
    order: list[str] = []

    original_commit = harness.session.commit

    async def tracking_commit():
        order.append("commit")
        return await original_commit()

    async def tracking_run(session, **kwargs):
        order.append("llm")
        return ValidatedResult({"mappings": [mapping()]}, 0.9, 10)

    harness.session.commit = AsyncMock(side_effect=tracking_commit)
    monkeypatch.setattr(tasks, "run", AsyncMock(side_effect=tracking_run))

    await tasks.run_mapping(harness.session, 3)

    assert "llm" in order, "模型没被调用，用例失去意义"
    assert order.index("commit") < order.index("llm"), (
        f"检查点之后、调用模型之前必须先提交释放锁；实际顺序 {order}"
    )


def _big_library():
    return [
        SimpleNamespace(id=n, code=f"C-{n:04d}", title="Identity management" if n % 7 == 0 else "Backup",
                        statement="Identities are managed." if n % 7 == 0 else "Backups run.")
        for n in range(1, 60)
    ]


async def test_flag_on_uses_jev_candidates(harness, monkeypatch):
    async def jev_rank(session, items, controls, *, top_k):
        return {item_id: [3, 5] for item_id, _ in items}, None

    load_vectors = AsyncMock(return_value=({}, {}))
    monkeypatch.setattr(tasks.jev_prefilter, "rank", jev_rank)
    monkeypatch.setattr(tasks, "load_vectors", load_vectors)
    harness.session.scalars = AsyncMock(side_effect=[_big_library(), harness.items])
    summary = await tasks.run_mapping(harness.session, 3)
    prompt = harness.run.await_args.kwargs["prompt"]
    assert summary["candidate_source"] == "jev"
    assert prompt.count("[control_id=") == 2 and "[control_id=3]" in prompt
    load_vectors.assert_not_awaited()


async def test_jev_fallback_uses_keyword_vector_candidates(harness, monkeypatch):
    monkeypatch.setattr(tasks.jev_prefilter, "rank", AsyncMock(return_value=(None, "call_failed")))
    monkeypatch.setattr(tasks, "load_vectors", AsyncMock(return_value=({}, {})))
    harness.session.scalars = AsyncMock(side_effect=[_big_library(), harness.items])
    summary = await tasks.run_mapping(harness.session, 3)
    prompt = harness.run.await_args.kwargs["prompt"]
    assert summary["candidate_source"] == "keyword_vector"
    assert summary["candidate_fallback_reason"] == "call_failed"
    assert "[control_id=7]" in prompt and "[control_id=1]" not in prompt


async def test_flag_off_reports_no_fallback_reason(harness, monkeypatch):
    monkeypatch.setattr(tasks, "load_vectors", AsyncMock(return_value=({}, {})))
    harness.session.scalars = AsyncMock(side_effect=[_big_library(), harness.items])
    summary = await tasks.run_mapping(harness.session, 3)
    assert summary["candidate_source"] == "keyword_vector"
    assert summary["candidate_fallback_reason"] is None


async def test_the_keyword_vector_prefilter_takes_the_current_top_k(harness, monkeypatch):
    # B1 要比较 TOP_K=20 与 40。rank_candidates 原来不传 top_k，用的是它定义时绑定的
    # 默认值——改 tasks.TOP_K 只改了「要不要预筛」，候选数仍是 20；Jev 那一路倒是传了。
    huge = [
        SimpleNamespace(id=n, code=f"C-{n:04d}", title="Identity management",
                        statement="Identities are managed. " + "x" * 100)
        for n in range(1, 200)
    ]
    monkeypatch.setattr(tasks, "TOP_K", 40)
    monkeypatch.setattr(tasks, "load_vectors", AsyncMock(return_value=({}, {})))
    harness.session.scalars = AsyncMock(side_effect=[huge, harness.items])
    await tasks.run_mapping(harness.session, 3)
    prompt = harness.run.await_args.kwargs["prompt"]
    assert prompt.count("[control_id=") == 40
