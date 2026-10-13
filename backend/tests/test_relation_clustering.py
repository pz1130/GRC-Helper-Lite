import pytest

from app.clauses.models import EMBEDDING_DIM, Clause
from app.controls.models import Control, ControlSource, SourceRelation
from app.ingest.models import DocStatus, DocType, Document
from app.relations.clustering import (
    Pair,
    batch_pairs,
    duplicate_pairs,
    section_clusters,
    split_cluster,
)
from app.relations.models import ControlEmbedding


def _vector(seed: float) -> list[float]:
    """构造方向可控的单位向量：seed 越接近，余弦距离越小。"""
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = 1.0
    vector[1] = seed
    return vector


def test_split_cluster_keeps_overlap_so_the_seam_is_not_cut():
    chunks = split_cluster(list(range(1, 8)), max_size=4, overlap=2)
    assert chunks == [[1, 2, 3, 4], [3, 4, 5, 6], [5, 6, 7]]


def test_split_cluster_leaves_small_clusters_alone():
    assert split_cluster([1, 2, 3], max_size=4, overlap=2) == [[1, 2, 3]]


def test_split_cluster_rejects_an_overlap_that_cannot_advance():
    with pytest.raises(ValueError):
        split_cluster([1, 2, 3, 4, 5], max_size=3, overlap=3)


async def _seed_similar(db_session):
    near = [Control(code=f"C-{n:04d}", title=f"t{n}", statement="s") for n in (1, 2)]
    far = Control(code="C-0003", title="t3", statement="s")
    db_session.add_all([*near, far])
    await db_session.flush()
    db_session.add_all([
        ControlEmbedding(control_id=near[0].id, embedding=_vector(0.01),
                         embedding_model="embo-01"),
        ControlEmbedding(control_id=near[1].id, embedding=_vector(0.02),
                         embedding_model="embo-01"),
        ControlEmbedding(control_id=far.id, embedding=_vector(9.0),
                         embedding_model="embo-01"),
    ])
    await db_session.flush()
    return near, far


@pytest.mark.asyncio
async def test_duplicate_pairs_returns_similar_pairs_once_and_ordered(db_session):
    near, far = await _seed_similar(db_session)
    pairs = await duplicate_pairs(db_session, top_k=8, min_similarity=0.9)

    assert [(p.low, p.high) for p in pairs] == [(near[0].id, near[1].id)], \
        "同一对不能出现两次，且 low < high"
    assert far.id not in {p.low for p in pairs} | {p.high for p in pairs}


@pytest.mark.asyncio
async def test_a_high_threshold_filters_everything_out(db_session):
    await _seed_similar(db_session)
    assert await duplicate_pairs(db_session, top_k=8, min_similarity=0.999999) == []


@pytest.mark.asyncio
async def test_controls_without_an_embedding_are_skipped(db_session):
    db_session.add(Control(code="C-0009", title="t", statement="s"))
    await db_session.flush()
    assert await duplicate_pairs(db_session) == []


async def _seed_sections(db_session):
    doc = Document(title="P", doc_type=DocType.PROCEDURE, file_hash="a" * 64,
                   file_path="/x.pdf", original_filename="x.pdf", status=DocStatus.ACTIVE)
    db_session.add(doc)
    await db_session.flush()
    made = []
    for index, (path, code) in enumerate([
        ("Change Management › Approval", "C-0001"),
        ("Change Management › Approval", "C-0002"),
        ("Change Management › Implementation", "C-0003"),
        ("Roles", "C-0004"),
    ]):
        clause = Clause(document_id=doc.id, number=str(index), heading="H",
                        heading_path=path, citation_label=str(index),
                        text="body", order_index=index, level=2)
        control = Control(code=code, title=code, statement="s")
        db_session.add_all([clause, control])
        await db_session.flush()
        db_session.add(ControlSource(control_id=control.id, clause_id=clause.id,
                                     relation=SourceRelation.DEFINES))
        made.append(control)
    await db_session.flush()
    return made


@pytest.mark.asyncio
async def test_section_clusters_group_by_document_and_top_heading(db_session):
    made = await _seed_sections(db_session)
    clusters = await section_clusters(db_session)

    grouped = {c.label: set(c.control_ids) for c in clusters}
    change = next(ids for label, ids in grouped.items() if "Change Management" in label)
    assert change == {made[0].id, made[1].id, made[2].id}, "顶层标题相同即同簇"
    # Roles 只有一个控制点，section_clusters 会丢掉它；这里只确认它没有并入 Change 簇。
    assert made[3].id not in change, "不同顶层标题不得并入同簇"


@pytest.mark.asyncio
async def test_a_single_control_section_is_dropped(db_session):
    """一个控制点组不成对，没有关系可推。"""
    made = await _seed_sections(db_session)
    clusters = await section_clusters(db_session)
    assert all(len(c.control_ids) >= 2 for c in clusters)
    assert made[3].id not in {i for c in clusters for i in c.control_ids}


@pytest.mark.asyncio
async def test_an_oversized_section_is_split_with_overlap(db_session):
    doc = Document(title="Big", doc_type=DocType.PROCEDURE, file_hash="b" * 64,
                   file_path="/y.pdf", original_filename="y.pdf", status=DocStatus.ACTIVE)
    db_session.add(doc)
    await db_session.flush()
    for index in range(10):
        clause = Clause(document_id=doc.id, number=str(index), heading="H",
                        heading_path="Section", citation_label=str(index),
                        text="body", order_index=index, level=2)
        control = Control(code=f"B-{index:04d}", title="t", statement="s")
        db_session.add_all([clause, control])
        await db_session.flush()
        db_session.add(ControlSource(control_id=control.id, clause_id=clause.id,
                                     relation=SourceRelation.DEFINES))
    await db_session.flush()

    clusters = await section_clusters(db_session, max_size=4, overlap=2)
    sizes = [len(c.control_ids) for c in clusters]
    assert max(sizes) <= 4
    assert len(clusters) > 1
    assert len({i for c in clusters for i in c.control_ids}) == 10, "切分不得丢条目"


# ---- 字符预算：只按条数封顶时，长 statement 的簇会渲染出远超预算的 prompt ----


def test_batch_pairs_honours_the_pair_count():
    pairs = [Pair(n, n + 100, 0.9) for n in range(1, 8)]
    assert [len(b) for b in batch_pairs(pairs, max_pairs=3)] == [3, 3, 1]


def test_batch_pairs_closes_a_batch_when_the_character_budget_runs_out():
    pairs = [Pair(1, 2, 0.9), Pair(3, 4, 0.9), Pair(5, 6, 0.9)]
    sizes = dict.fromkeys(range(1, 7), 400)      # 每对 800 字
    batches = batch_pairs(pairs, sizes=sizes, max_pairs=25, max_chars=1700)
    assert [len(b) for b in batches] == [2, 1], "预算先于对数触顶时按预算切"


def test_a_single_oversized_pair_gets_its_own_batch_rather_than_being_dropped():
    pairs = [Pair(1, 2, 0.9), Pair(3, 4, 0.9)]
    sizes = {1: 50, 2: 50, 3: 9000, 4: 9000}
    batches = batch_pairs(pairs, sizes=sizes, max_chars=1000)
    assert [[p.low for p in b] for b in batches] == [[1], [3]]


def test_batch_pairs_on_an_empty_candidate_list():
    assert batch_pairs([]) == []


def test_split_cluster_splits_on_the_character_budget_below_the_size_cap():
    ids = list(range(1, 7))
    sizes = dict.fromkeys(ids, 500)
    chunks = split_cluster(ids, max_size=20, overlap=1, sizes=sizes, max_chars=1500)
    assert chunks == [[1, 2, 3], [3, 4, 5], [5, 6]]
    assert {i for c in chunks for i in c} == set(ids), "切分不得丢条目"


def test_split_cluster_without_sizes_is_unchanged():
    """不给 sizes 时字符预算不参与，老行为逐字保持。"""
    assert split_cluster(list(range(1, 8)), max_size=4, overlap=2) == [
        [1, 2, 3, 4], [3, 4, 5, 6], [5, 6, 7]
    ]


def test_split_cluster_rejects_a_non_positive_budget():
    with pytest.raises(ValueError):
        split_cluster([1, 2, 3], max_size=2, overlap=1, max_chars=0)


# ---- 跨模型不比、并列名次不飘 ----


@pytest.mark.asyncio
async def test_vectors_from_different_models_are_never_compared(db_session):
    """换模型时的回填是逐批落盘的，中途必然新旧并存；跨模型的余弦距离无意义。"""
    left = Control(code="C-0001", title="t1", statement="s")
    right = Control(code="C-0002", title="t2", statement="s")
    db_session.add_all([left, right])
    await db_session.flush()
    db_session.add_all([
        ControlEmbedding(control_id=left.id, embedding=_vector(0.01),
                         embedding_model="embo-01"),
        ControlEmbedding(control_id=right.id, embedding=_vector(0.02),
                         embedding_model="embo-02"),
    ])
    await db_session.flush()

    assert await duplicate_pairs(db_session, min_similarity=0.5) == []


@pytest.mark.asyncio
async def test_pairs_that_tie_on_similarity_come_back_in_a_fixed_order(db_session):
    """批次指纹是对渲染后的 prompt 算的：名次一飘，重试时检查点就全部落空。"""
    controls = [Control(code=f"C-{n:04d}", title=f"t{n}", statement="s") for n in (1, 2, 3)]
    db_session.add_all(controls)
    await db_session.flush()
    # 2 与 3 相对 1 完全对称，sim(1,2) 与 sim(1,3) 逐位相等。
    for control, seed in zip(controls, (0.0, 0.05, -0.05), strict=True):
        db_session.add(ControlEmbedding(control_id=control.id, embedding=_vector(seed),
                                        embedding_model="embo-01"))
    await db_session.flush()

    pairs = await duplicate_pairs(db_session, min_similarity=0.9)
    one, two, three = (c.id for c in controls)
    assert pairs[0].similarity == pairs[1].similarity, "构造的就是并列"
    assert [(p.low, p.high) for p in pairs] == [(one, two), (one, three), (two, three)]
