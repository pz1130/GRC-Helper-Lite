import pytest

"""候选生成的纯逻辑测试；向量查询在 test_conflicts_router.py 里连库验。"""

from app.conflicts.clustering import MIN_SIMILARITY, TOP_K_NEIGHBOURS, keep_cross_document
from app.relations.clustering import Pair


def test_the_starting_threshold_is_below_the_duplicates_one():
    # M6 的 0.90 要求「几乎是同一条」；冲突只要求「在谈同一件事」，门槛应更低。
    from app.relations.clustering import MIN_SIMILARITY as DUPLICATES_THRESHOLD

    assert MIN_SIMILARITY == 0.85
    assert MIN_SIMILARITY < DUPLICATES_THRESHOLD


def test_conflicts_search_beyond_the_eight_neighbours_used_for_duplicates():
    assert TOP_K_NEIGHBOURS == 20


def test_pairs_inside_one_document_are_dropped():
    pairs = [Pair(1, 2, 0.95), Pair(1, 3, 0.91)]
    # 控制点 1 与 2 同属文件 10；1 与 3 分属 10 与 11。
    docs = {1: {10}, 2: {10}, 3: {11}}

    assert keep_cross_document(pairs, docs) == [Pair(1, 3, 0.91)]


def test_a_control_spanning_two_documents_still_pairs_across():
    # 控制点 1 横跨两份文件，与只在文件 10 的控制点 2 之间仍算跨文件。
    docs = {1: {10, 11}, 2: {10}}

    assert keep_cross_document([Pair(1, 2, 0.9)], docs) == [Pair(1, 2, 0.9)]


def test_a_control_without_any_source_is_dropped():
    # 没有支撑条款就没有原文可比，留着只会在第二级炸掉。
    docs = {1: {10}, 2: set()}

    assert keep_cross_document([Pair(1, 2, 0.99)], docs) == []


async def _control_sourced_in(db_session, document, code):
    from app.clauses.models import Clause
    from app.controls.models import Control, ControlSource, SourceRelation

    clause = Clause(
        document_id=document.id, number="1", heading="H", heading_path="H",
        citation_label="1", text=f"{code} text", order_index=0, level=1,
    )
    control = Control(code=code, title=code)
    db_session.add_all([clause, control])
    await db_session.flush()
    db_session.add(
        ControlSource(control_id=control.id, clause_id=clause.id, relation=SourceRelation.DEFINES)
    )
    await db_session.flush()
    return control


@pytest.mark.asyncio
async def test_a_superseded_document_is_not_a_side_of_a_conflict(db_session):
    # 旧版上的控制点和新版抽出来的相似控制点「跨文件、高度相似」，正好会被当成
    # 冲突候选——那是同一份制度的两个版本，不是两份制度互相矛盾。
    from app.conflicts.clustering import Pair, clause_bundles, documents_of
    from app.ingest.models import DocStatus, DocType, Document

    def doc(name, status):
        return Document(
            title=name, doc_type=DocType.POLICY, file_hash=name.ljust(64, "0"),
            file_path=f"/{name}.pdf", original_filename=f"{name}.pdf", status=status,
        )

    old, new = doc("old", DocStatus.SUPERSEDED), doc("new", DocStatus.ACTIVE)
    db_session.add_all([old, new])
    await db_session.flush()
    old_control = await _control_sourced_in(db_session, old, "OLD-1")
    new_control = await _control_sourced_in(db_session, new, "NEW-1")

    documents = await documents_of(db_session)
    assert old_control.id not in documents
    assert documents[new_control.id] == {new.id}

    bundles = await clause_bundles(db_session, [Pair(old_control.id, new_control.id, 0.9)])
    assert old_control.id not in bundles
