"""按语料标定的参数与自动标定。

这些值依赖具体语料：MIN_SIMILARITY 是拿一份 136 个控制点的语料标出来的，
换一份文档集就不成立。写死在常量里意味着换客户要改代码，而且没人知道改成多少。
"""

import pytest
from sqlalchemy import select

from app.controls.models import Control
from app.llm.models import AppSetting
from app.relations import clustering, params
from app.relations.calibration import MARGIN, MIN_GROUND_TRUTH_PAIRS, calibrate
from app.relations.models import ControlEmbedding


def _vector(seed: float) -> list[float]:
    from app.clauses.models import EMBEDDING_DIM

    v = [0.0] * EMBEDDING_DIM
    v[0] = 1.0
    v[1] = seed
    return v


async def _set(db_session, name: str, value) -> None:
    db_session.add(AppSetting(key=params.PREFIX + name, value={"value": value}))
    await db_session.flush()


@pytest.mark.asyncio
async def test_defaults_are_the_module_constants(db_session):
    cfg = await params.load(db_session)
    assert cfg.min_similarity == clustering.MIN_SIMILARITY
    assert cfg.top_k == clustering.TOP_K_NEIGHBOURS
    assert cfg.max_batch_chars == clustering.MAX_BATCH_CHARS


@pytest.mark.asyncio
async def test_a_stored_value_overrides_the_default(db_session):
    await _set(db_session, "min_similarity", 0.82)
    assert (await params.load(db_session)).min_similarity == 0.82


@pytest.mark.asyncio
# NaN 不在列内：JSONB 存不了它，写入阶段就会被数据库拒绝。
# _value 里的 isfinite 仍然留着——它防的是别的来源，不是这条路径。
@pytest.mark.parametrize("bad", [1.5, -0.1, "abc", None, True])
async def test_an_unusable_value_falls_back_instead_of_breaking_the_run(db_session, bad):
    """配置坏了应当退回默认值，而不是让整轮推断崩在半路。"""
    await _set(db_session, "min_similarity", bad)
    assert (await params.load(db_session)).min_similarity == clustering.MIN_SIMILARITY


@pytest.mark.asyncio
async def test_overlap_not_smaller_than_cluster_size_falls_back(db_session):
    """两个值各自合法、组合起来会让 split_cluster 抛异常——只有配置项之间才有的失效。"""
    await _set(db_session, "max_cluster_size", 5)
    await _set(db_session, "cluster_overlap", 5)
    cfg = await params.load(db_session)
    assert cfg.cluster_overlap < cfg.max_cluster_size


async def _controls(db_session, spec: list[tuple[str, str, float | None]]) -> None:
    """spec: (code, statement, 向量 seed；None 表示不向量化)。"""
    for code, statement, seed in spec:
        control = Control(code=code, title=code, statement=statement)
        db_session.add(control)
        await db_session.flush()
        if seed is not None:
            db_session.add(ControlEmbedding(control_id=control.id, embedding=_vector(seed),
                                            embedding_model="embo-01", embedding_version="v2"))
    await db_session.flush()


BASE = ("Exceptions to the policy must be reasonably justified and handled "
        "in accordance with the defined roles and responsibilities of the bank.")


def _reworded(n: int) -> str:
    """同一段要求的不同措辞：词集高度重合，但不是逐字相同。"""
    return BASE.replace("must be", "shall be").replace("the bank", f"the bank unit {n}")


@pytest.mark.asyncio
async def test_calibration_finds_near_duplicates_by_word_overlap(db_session):
    """免费正样本靠归一化词集的 Jaccard 找，不需要人工标注。"""
    await _controls(db_session, [
        (f"C-{n}", _reworded(n), 0.01 * n) for n in range(1, 8)
    ])
    result = await calibrate(db_session)

    assert len(result.pairs) >= MIN_GROUND_TRUTH_PAIRS
    assert result.recommended is not None
    assert all(not same for *_, same in result.pairs), "这批措辞各不相同"


@pytest.mark.asyncio
async def test_identical_statements_are_counted_but_do_not_set_the_floor(db_session):
    """逐字相同的对 embedding 完全相同、相似度恒为 1.0。

    只拿它们标定会得出高得离谱的阈值——真实语料上是 0.96，而该语料真实的近重复
    低到 0.942，按 0.96 走就会漏掉。它们计入分布，但不能决定下界。
    """
    await _controls(db_session, [(f"S-{n}", BASE, 0.02) for n in range(1, 5)])
    await _controls(db_session, [(f"R-{n}", _reworded(n), 0.3 + 0.05 * n) for n in range(1, 5)])
    result = await calibrate(db_session)

    identical = [p for p in result.pairs if p[3]]
    assert identical and all(s == pytest.approx(1.0) for *_, s, _ in identical)
    assert result.recommended is not None
    assert result.recommended < 1.0 - MARGIN, "不能被恒为 1.0 的退化样本顶到天花板"


def test_quantile_ignores_the_bottom_outliers():
    """真实语料上有词汇高度重合却语义无关的对（余弦 0.42）。取 min 会被它拖死。"""
    from app.relations.calibration import quantile

    scores = sorted([0.42, 0.67] + [0.94 + 0.005 * n for n in range(18)])
    assert min(scores) == 0.42
    assert quantile(scores, 0.10) > 0.9, "10% 分位点应把两个离群点排除在外"
    assert quantile(scores, 0.0) == 0.42, "0% 分位点就是最小值"


OTHER = ("Privileged accounts require quarterly recertification by the "
         "information security team using the approved review workflow tooling.")


@pytest.mark.asyncio
async def test_an_outlier_pair_does_not_drag_the_threshold_to_the_floor(db_session):
    """离群的是**一对**，占比与真实语料相当（42 对里 2 对）。

    早先版本让一个离群控制点和其余每一个都配对，45 对里 9 对是它——20% 的离群率，
    分位点自然落进离群区。那是测试构造的问题，不是算法的。
    """
    # 主群：6 条措辞相近、向量也相近 → 15 对高相似度
    await _controls(db_session, [(f"C-{n}", _reworded(n), 0.01 * n) for n in range(1, 7)])
    # 离群对：彼此措辞相近（会被 Jaccard 抓成正样本），但向量方向差很远
    await _controls(db_session, [
        ("ODD-1", OTHER, 0.0),
        ("ODD-2", OTHER.replace("quarterly", "annual"), 60.0),
    ])
    result = await calibrate(db_session)

    scores = sorted(s for *_, s, _ in result.pairs)
    assert scores[0] < 0.5, "构造的离群对应当在样本里"
    assert result.recommended is not None and result.recommended > 0.8, \
        "分位数应当把离群对排除在外"
    assert result.as_dict()["ground_truth_below_threshold"], "被排除的正样本要如实列出"


@pytest.mark.asyncio
async def test_calibration_refuses_when_the_corpus_has_no_ground_truth(db_session):
    """标不出来就不写——沿用别人语料的阈值比没有阈值更糟，它看起来像个结论。"""
    await _controls(db_session, [
        ("C-9", "A" * 60 + " alpha bravo charlie delta echo", 0.1),
        ("C-8", "B" * 60 + " foxtrot golf hotel india juliet", 0.2),
    ])
    result = await calibrate(db_session)
    assert result.recommended is None
    assert str(MIN_GROUND_TRUTH_PAIRS) in result.reason


@pytest.mark.asyncio
async def test_unembedded_ground_truth_is_reported_as_actionable(db_session):
    """有正样本但没向量化，是可操作的状态，不能和「语料里没有正样本」混为一谈。"""
    await _controls(db_session, [(f"C-{n}", _reworded(n), None) for n in range(1, 8)])
    result = await calibrate(db_session)
    assert result.recommended is None
    assert "向量化" in result.reason


@pytest.mark.asyncio
async def test_calibrate_endpoint_writes_the_threshold_and_audits(client, db_session):
    from app.iam.models import AuditLog, User
    from app.iam.permissions import Role
    from app.iam.security import hash_password

    db_session.add(User(email="lead@example.com", name="L", role=Role.GRC_LEAD,
                        password_hash=hash_password("pw123456")))
    await db_session.flush()
    await _controls(db_session, [(f"C-{n}", _reworded(n), 0.01 * n) for n in range(1, 8)])
    token = (await client.post("/api/auth/login",
                               json={"email": "lead@example.com", "password": "pw123456"})
             ).json()["access_token"]

    body = (await client.post("/api/relations/calibrate",
                              headers={"Authorization": f"Bearer {token}"})).json()

    assert body["applied"] is True
    stored = await db_session.get(AppSetting, params.PREFIX + "min_similarity")
    assert stored.value["value"] == body["recommended_min_similarity"]
    actions = list(await db_session.scalars(select(AuditLog.action)))
    assert "relations.calibrate" in actions
