import pytest

from app.clauses.models import Clause, ClauseChunk
from app.ingest.models import DocType, Document
from app.search.fulltext import search


async def _chunks(db_session, bodies: list[str], *, doc_title: str = "P") -> Document:
    doc = Document(
        title=doc_title, doc_type=DocType.PROCEDURE,
        file_hash=doc_title.ljust(64, "x"), file_path="/x.pdf", original_filename="x.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id, number="1", heading="S", heading_path="Doc › S",
        citation_label="1", text="", order_index=0, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    for index, body in enumerate(bodies):
        db_session.add(
            ClauseChunk(
                clause_id=clause.id, document_id=doc.id, chunk_index=index, text=body
            )
        )
    await db_session.flush()
    return doc


@pytest.mark.asyncio
async def test_finds_a_matching_chunk(db_session):
    await _chunks(db_session, ["Privileged accounts must be reviewed quarterly."])
    hits = await search(db_session, "privileged account review")
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_ranks_are_dense_and_start_at_one(db_session):
    await _chunks(
        db_session,
        [
            "Privileged access management with VaultKeeper.",
            "Privileged accounts and privileged access reviews.",
            "Change management process.",
        ],
    )
    hits = await search(db_session, "privileged access")
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


@pytest.mark.asyncio
async def test_stemming_matches_word_forms(db_session):
    await _chunks(db_session, ["Accounts are reviewed by the owner."])
    assert await search(db_session, "review") != []


@pytest.mark.asyncio
async def test_no_match_returns_empty(db_session):
    await _chunks(db_session, ["Change management process."])
    assert await search(db_session, "kubernetes") == []


@pytest.mark.asyncio
async def test_quoted_phrase_is_honoured(db_session):
    await _chunks(
        db_session,
        ["privileged access management", "access is privileged only in emergencies"],
    )
    hits = await search(db_session, '"privileged access"')
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_negation_is_honoured(db_session):
    await _chunks(db_session, ["privileged access with VaultKeeper", "privileged access manual"])
    hits = await search(db_session, "privileged -vaultkeeper")
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_document_filter_narrows_results(db_session):
    first = await _chunks(db_session, ["privileged access one"], doc_title="A")
    await _chunks(db_session, ["privileged access two"], doc_title="B")
    hits = await search(db_session, "privileged access", document_id=first.id)
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_limit_is_respected(db_session):
    await _chunks(db_session, [f"privileged access number {i}" for i in range(10)])
    assert len(await search(db_session, "privileged", limit=3)) == 3


@pytest.mark.asyncio
async def test_empty_query_returns_empty_without_touching_the_database(db_session):
    await _chunks(db_session, ["privileged access"])
    assert await search(db_session, "   ") == []


# ── AND 语义缺陷的回归测试 ──────────────────────────────────
# 实测：websearch_to_tsquery 是合取语义，问句里只要有一个词不在语料中，
# 整句返回 0 条。修复前 8 条黄金查询里 4 条完全无召回，Recall@5 = 0.25。


@pytest.mark.asyncio
async def test_question_still_recalls_when_one_word_is_absent(db_session):
    """核心回归：'rotated' 不在语料里，不能因此让整句归零。"""
    await _chunks(db_session, ["Privileged accounts are vaulted in VaultKeeper."])

    hits = await search(db_session, "how are privileged accounts stored and rotated")
    assert hits, "问句里有一个语料中不存在的词，不该让整句无召回"


@pytest.mark.asyncio
async def test_natural_language_question_recalls(db_session):
    await _chunks(
        db_session,
        [
            "Threat intelligence sources are reviewed by the owner.",
            "Change requests are approved by the CAB.",
        ],
    )
    assert await search(db_session, "who owns the threat intelligence process") != []


@pytest.mark.asyncio
async def test_more_matching_terms_ranks_higher(db_session):
    """OR 语义靠 ts_rank 排序：命中词多的应当排在前面。"""
    await _chunks(
        db_session,
        [
            "The CAB approves changes.",
            "Privileged accounts are stored and reviewed by the account owner.",
        ],
    )
    hits = await search(db_session, "privileged accounts stored owner")
    assert hits[0].rank == 1
    assert len(hits) >= 1


@pytest.mark.asyncio
async def test_quoted_phrase_still_uses_conjunctive_semantics(db_session):
    """用户打了引号就是要精确，这时仍走 websearch_to_tsquery。"""
    await _chunks(
        db_session,
        ["privileged access management", "access is privileged only in emergencies"],
    )
    assert len(await search(db_session, '"privileged access"')) == 1


@pytest.mark.asyncio
async def test_exclusion_still_works(db_session):
    await _chunks(db_session, ["privileged access with VaultKeeper", "privileged access manual"])
    assert len(await search(db_session, "privileged -vaultkeeper")) == 1


@pytest.mark.asyncio
async def test_hyphenated_word_is_not_mistaken_for_exclusion(db_session):
    """'multi-factor' 里的连字符不是排除语法，不该被误判成精确模式。"""
    await _chunks(db_session, ["Multi-factor authentication is required."])
    assert await search(db_session, "multi-factor authentication nonexistentword") != []


@pytest.mark.asyncio
async def test_completely_unmatched_query_still_returns_empty(db_session):
    """放宽成 OR 之后，也不能变成"什么都能命中"。"""
    await _chunks(db_session, ["Change management process."])
    assert await search(db_session, "kubernetes istio helm") == []
