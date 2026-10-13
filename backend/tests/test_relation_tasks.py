"""纯编排测试：不连数据库，也不用共享 fixture。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.errors import AppError
from app.llm.providers.base import ProviderError
from app.llm.runner import ValidatedResult
from app.llm.validation import ValidationFailure
from app.relations import tasks
from app.relations.clustering import Cluster, Pair
from app.worker import LONG_JOB_TIMEOUT, WorkerSettings


def relation(**overrides):
    return {
        "from_control_id": 1, "to_control_id": 2,
        "from_quote": "a", "to_quote": "b",
        "rationale": "because they overlap", "confidence": 0.8,
        **overrides,
    }


@pytest.fixture
def harness(monkeypatch):
    controls = [
        SimpleNamespace(id=1, code="C-0001", title="Approval", statement="a"),
        SimpleNamespace(id=2, code="C-0002", title="Implementation", statement="b"),
    ]
    call = SimpleNamespace(id=10, redaction_hits={"dictionary": 2})

    session = MagicMock()

    async def get(model, key):
        # 按类型分派：此前对任何 get 都返回同一个 LLMCall 替身，
        # _build_batches 查 AppSetting 时就会拿到一个没有 .value 的对象。
        # None 表示「该参数没有覆盖值」，正是未标定语料的真实状态。
        from app.llm.models import AppSetting

        return None if model is AppSetting else call

    session.get = AsyncMock(side_effect=get)
    session.scalars = AsyncMock(return_value=controls)
    session.scalar = AsyncMock(return_value=None)   # 无检查点
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()

    @asynccontextmanager
    async def savepoint():
        yield None

    session.begin_nested = savepoint

    monkeypatch.setattr(
        tasks, "duplicate_pairs", AsyncMock(return_value=[Pair(1, 2, 0.91)]))
    monkeypatch.setattr(
        tasks, "section_clusters", AsyncMock(return_value=[Cluster([1, 2], "doc1:Change")]))
    run = AsyncMock(return_value=ValidatedResult({"relations": [relation()]}, 0.9, 10))
    create = AsyncMock(side_effect=lambda *a, **k: SimpleNamespace(id=21))
    monkeypatch.setattr(tasks, "run", run)
    monkeypatch.setattr(tasks.review_service, "create", create, raising=False)
    monkeypatch.setattr(
        tasks.RelationCitationValidator, "check", AsyncMock(return_value=None), raising=False)
    return SimpleNamespace(session=session, controls=controls, call=call,
                           run=run, create=create)


async def test_both_channels_run_and_each_fixes_its_relation_type(harness):
    summary = await tasks.run_inference(harness.session, run_key="t1")

    assert summary["batches"] == 2          # 一对批 + 一簇批
    assert summary["proposals"] == 2
    kinds = {call.kwargs["kind"].value for call in harness.create.await_args_list}
    assert kinds == {"relation"}
    types = {
        call.kwargs["payload"]["relation_type"]
        for call in harness.create.await_args_list
    }
    assert types == {"duplicates", "depends_on"}, "关系类型由通道决定，不由模型给出"


async def test_the_duplicate_prompt_differs_from_the_dependency_prompt(harness):
    await tasks.run_inference(harness.session)
    systems = {call.kwargs["system"] for call in harness.run.await_args_list}
    assert len(systems) == 2


async def test_a_rejected_batch_is_counted_not_silently_dropped(harness, monkeypatch):
    monkeypatch.setattr(
        tasks, "run", AsyncMock(side_effect=ValidationFailure("模型输出中没有找到合法的 JSON 对象")))
    summary = await tasks.run_inference(harness.session)
    assert summary["rejected"] == 2 and summary["proposals"] == 0


async def test_a_provider_error_fails_the_batch_without_killing_the_run(harness, monkeypatch):
    calls = {"n": 0}

    async def flaky(session, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("网络错误: ReadTimeout", retryable=True)
        return ValidatedResult({"relations": [relation()]}, 0.9, 10)

    monkeypatch.setattr(tasks, "run", AsyncMock(side_effect=flaky))
    summary = await tasks.run_inference(harness.session)
    assert summary["failed"] == 1
    assert summary["rejected"] == 0
    assert summary["completed_batches"] == 1


async def test_one_bad_citation_drops_only_that_relation(harness, monkeypatch):
    good, bad = relation(), relation(to_control_id=999)
    monkeypatch.setattr(
        tasks, "run",
        AsyncMock(return_value=ValidatedResult({"relations": [good, bad]}, 0.9, 10)))

    async def check(self, payload):
        bad_entries = [e for e in payload["relations"] if e["to_control_id"] != 2]
        return "控制点 999 不属于本批次" if bad_entries else None

    monkeypatch.setattr(tasks.RelationCitationValidator, "check", check)
    summary = await tasks.run_inference(harness.session)

    assert summary["dropped_relations"] == 2   # 两条通道各丢一条
    assert summary["rejected"] == 0


async def test_the_checkpoint_lock_is_released_before_the_model_call(harness, monkeypatch):
    order: list[str] = []
    original = harness.session.commit

    async def tracking_commit():
        order.append("commit")
        return await original()

    async def tracking_run(session, **kwargs):
        order.append("llm")
        return ValidatedResult({"relations": [relation()]}, 0.9, 10)

    harness.session.commit = AsyncMock(side_effect=tracking_commit)
    monkeypatch.setattr(tasks, "run", AsyncMock(side_effect=tracking_run))
    await tasks.run_inference(harness.session)

    assert order.index("commit") < order.index("llm"), (
        f"advisory 锁不得跨越 LLM 调用；实际顺序 {order}"
    )


async def test_an_empty_control_library_refuses_to_run(harness, monkeypatch):
    monkeypatch.setattr(tasks, "duplicate_pairs", AsyncMock(return_value=[]))
    monkeypatch.setattr(tasks, "section_clusters", AsyncMock(return_value=[]))
    harness.session.scalars = AsyncMock(return_value=[])
    with pytest.raises(AppError):
        await tasks.run_inference(harness.session)


async def test_the_same_pair_in_two_overlapping_batches_is_proposed_once(harness, monkeypatch):
    """章节簇是带重叠切分的，重叠区里的每一对会在两个批次各判一次。

    不去重就是两条一模一样的待确认提案：审核者确认了第一条，第二条只会在
    materialize 里撞 Conflict，只能手工拒绝。
    """
    monkeypatch.setattr(tasks, "duplicate_pairs", AsyncMock(return_value=[]))
    monkeypatch.setattr(tasks, "section_clusters", AsyncMock(return_value=[
        Cluster([1, 2], "doc1:Change #1"),
        Cluster([2, 1], "doc1:Change #2"),      # 顺序不同，渲染与指纹都不同
    ]))
    summary = await tasks.run_inference(harness.session)

    assert summary["batches"] == 2
    assert summary["proposals"] == 1
    assert summary["duplicate_relations"] == 1


async def test_a_mirrored_duplicate_is_not_proposed_twice(harness, monkeypatch):
    """duplicates 对称：1→2 与 2→1 是同一条，落库前才规范化就太晚了。"""
    monkeypatch.setattr(tasks, "section_clusters", AsyncMock(return_value=[]))
    monkeypatch.setattr(tasks, "run", AsyncMock(return_value=ValidatedResult(
        {"relations": [relation(), relation(from_control_id=2, to_control_id=1)]}, 0.9, 10)))
    summary = await tasks.run_inference(harness.session)

    assert summary["proposals"] == 1
    assert summary["duplicate_relations"] == 1


async def test_opposite_directions_of_depends_on_are_both_kept(harness, monkeypatch):
    """depends_on 有向：1→2 与 2→1 是两条不同的主张，不能当重复吃掉。"""
    monkeypatch.setattr(tasks, "duplicate_pairs", AsyncMock(return_value=[]))
    monkeypatch.setattr(tasks, "run", AsyncMock(return_value=ValidatedResult(
        {"relations": [relation(), relation(from_control_id=2, to_control_id=1)]}, 0.9, 10)))
    summary = await tasks.run_inference(harness.session)

    assert summary["proposals"] == 2
    assert summary["duplicate_relations"] == 0


async def test_the_duplicates_channel_passes_its_candidate_pairs_to_gate_three(harness):
    """一批 25 对、50 个控制点，闸 3 必须知道哪两个是并排给出的。"""
    batches = await tasks._build_batches(harness.session)
    duplicates = [b for b in batches if b.relation_type == "duplicates"]
    depends = [b for b in batches if b.relation_type == "depends_on"]

    assert duplicates and duplicates[0].allowed_pairs == (frozenset({1, 2}),)
    assert depends and depends[0].allowed_pairs is None, "整簇一起判，没有对可核"



@pytest.mark.parametrize(
    "name", ["infer_relations", "map_framework", "extract_controls", "reindex_all"]
)
def test_every_batched_provider_task_declares_its_own_timeout(name):
    """ARQ 读 Function.timeout_s；挂在 coroutine 上的 timeout 属性是无效的。

    裸注册只吃得到 job_timeout=900，而这些任务逐批调 provider，M5 记录过一次
    映射跑了 51 分钟——15 分钟被杀、max_tries 用完就永远跑不完。
    """
    registered = next(
        f for f in WorkerSettings.functions if getattr(f, "name", None) == name
    )
    assert registered.timeout_s == LONG_JOB_TIMEOUT
    assert LONG_JOB_TIMEOUT > WorkerSettings.job_timeout == 900
