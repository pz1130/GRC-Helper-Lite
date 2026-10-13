from types import SimpleNamespace

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource, SourceRelation
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.impact.service import pair_clauses
from app.ingest.models import DocType, Document


async def _user(db_session, role: Role = Role.VIEWER, email: str = "v@example.com") -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str = "v@example.com") -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _document(db_session, title: str, index: int, **extra) -> Document:
    doc = Document(
        title=title, doc_type=DocType.POLICY, file_hash=str(index) * 64,
        file_path=f"/{index}.pdf", original_filename=f"{index}.pdf", **extra,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


async def _clause(db_session, document_id: int, number: str, text: str, order_index: int = 0) -> Clause:
    clause = Clause(
        document_id=document_id, number=number, heading="H",
        heading_path=f"H › {number}", citation_label=number,
        text=text, order_index=order_index, level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _control(db_session, code: str, clause_id: int) -> Control:
    """建一个控制点并把它挂到一条条款上。"""
    control = Control(code=code, title=f"Control {code}", statement=f"{code} statement.")
    db_session.add(control)
    await db_session.flush()
    db_session.add(ControlSource(
        control_id=control.id, clause_id=clause_id, relation=SourceRelation.DEFINES))
    await db_session.flush()
    return control


async def _two_documents_in_conflict(db_session):
    """两份文件各一条条款、各一个控制点——冲突派生用例的最小语料。

    返回 (clause_a, clause_b, control_a, control_b)。
    """
    doc_a = await _document(db_session, "Password Policy", 1)
    doc_b = await _document(db_session, "Access Standard", 2)
    clause_a = await _clause(db_session, doc_a.id, "4.2", "Passwords rotate every 90 days.")
    clause_b = await _clause(db_session, doc_b.id, "7.1", "Passwords rotate every 180 days.")
    control_a = await _control(db_session, "C-0001", clause_a.id)
    control_b = await _control(db_session, "C-0002", clause_b.id)
    return clause_a, clause_b, control_a, control_b


def _pair(clause_id: int, number: str | None, heading_path: str):
    return SimpleNamespace(id=clause_id, number=number, heading_path=heading_path)


def test_clauses_with_the_same_number_and_path_are_matched():
    old = [_pair(1, "4.2", "Password › Rotation")]
    new = [_pair(11, "4.2", "Password › Rotation")]

    result = pair_clauses(old, new)

    assert result.matched == [(1, 11)]
    assert result.removed == [] and result.added == []


def test_a_clause_missing_from_the_new_version_is_removed():
    old = [_pair(1, "4.2", "Password › Rotation")]
    new = []

    result = pair_clauses(old, new)

    assert result.removed == [1] and result.matched == []


def test_a_clause_only_in_the_new_version_is_added():
    result = pair_clauses([], [_pair(11, "4.3", "Password › Strength")])

    assert result.added == [11] and result.matched == []


def test_the_same_number_under_a_different_heading_is_not_a_match():
    # 只按 number 配会把改章节的条款错配。宁可两边都列出来让人自己看。
    old = [_pair(1, "4.2", "Password › Rotation")]
    new = [_pair(11, "4.2", "Access › Rotation")]

    result = pair_clauses(old, new)

    assert result.matched == []
    assert result.removed == [1] and result.added == [11]


def test_pairing_is_deterministic_regardless_of_input_order():
    old = [_pair(2, "4.3", "P › B"), _pair(1, "4.2", "P › A")]
    new = [_pair(12, "4.3", "P › B"), _pair(11, "4.2", "P › A")]

    assert pair_clauses(old, new).matched == [(1, 11), (2, 12)]


def test_missing_legacy_numbers_fall_back_to_the_same_heading_path():
    old = [_pair(1, None, "Password › Rotation")]
    new = [_pair(11, "4.2", "Password › Rotation")]

    result = pair_clauses(old, new)

    assert result.matched == [(1, 11)]
    assert result.removed == [] and result.added == []


def test_changed_numbers_do_not_fall_back_when_both_sides_have_numbers():
    old = [_pair(1, "4.2", "Password › Rotation")]
    new = [_pair(11, "4.3", "Password › Rotation")]

    result = pair_clauses(old, new)

    assert result.matched == []
    assert result.removed == [1] and result.added == [11]


def test_legacy_path_can_gain_a_parent_when_its_leaf_heading_is_unique():
    old = [_pair(1, None, "Roles › Incident Handling")]
    new = [_pair(11, "3.4.2", "Incident Process › Reporting › Incident Handling")]

    assert pair_clauses(old, new).matched == [(1, 11)]


def test_duplicate_leaf_headings_are_not_guessed():
    old = [
        _pair(1, None, "Access › Approval"),
        _pair(2, None, "Change › Approval"),
    ]
    new = [_pair(11, "3.1", "Process › Approval")]

    result = pair_clauses(old, new)

    assert result.matched == []
    assert result.removed == [1, 2] and result.added == [11]


async def test_a_document_without_a_previous_version_is_a_400(client, db_session):
    # 夹具照抄 tests/test_graph.py
    document = await _document(db_session, "Password Policy", 1)
    await _user(db_session, email="v@example.com")
    headers = await _auth(client, "v@example.com")

    resp = await client.get(
        f"/api/documents/{document.id}/change-impact", headers=headers)

    assert resp.status_code == 400


async def test_the_report_lists_controls_hanging_off_removed_clauses(client, db_session):
    old_doc = await _document(db_session, "Password Policy v1", 1)
    old_clause = await _clause(db_session, old_doc.id, "4.2", "Rotate every 90 days.")
    control = await _control(db_session, "C-0001", old_clause.id)
    framework = Framework(
        key="csf", name_zh="CSF", name_en="CSF", version="2.0", source="nist", item_count=1,
    )
    db_session.add(framework)
    await db_session.flush()
    item = FrameworkItem(
        framework_id=framework.id, code="PR.AA-01", title="Identities",
        description="", level=1, order_index=0,
    )
    evidence_type = EvidenceType(
        name_zh="导出", name_en="Export", format="csv",
        cadence=EvidenceCadence.QUARTERLY, typical_source="AD",
    )
    db_session.add_all([item, evidence_type])
    await db_session.flush()
    db_session.add(Mapping(
        control_id=control.id, framework_item_id=item.id,
        strength=MappingStrength.PARTIAL, rationale="", quote="",
    ))
    db_session.add(EvidenceItem(
        evidence_type_id=evidence_type.id, control_id=control.id,
        title="AD rotation export", status=EvidenceStatus.PLANNED,
    ))
    new_doc = await _document(db_session, "Password Policy v2", 2, supersedes_id=old_doc.id)
    # 新版没有 4.2，只有一条新增的 4.4 —— 旧的 4.2 因此进 removed
    await _clause(db_session, new_doc.id, "4.4", "Passwords must not be reused.")
    await _user(db_session)
    headers = await _auth(client)
    await db_session.flush()

    resp = await client.get(
        f"/api/documents/{new_doc.id}/change-impact", headers=headers)

    body = resp.json()
    assert body["removed"][0]["clause_id"] == old_clause.id
    assert body["affected_controls"][0]["id"] == control.id
    assert body["affected_controls"][0]["code"] == "C-0001"
    assert body["affected_mappings"][0]["control_code"] == "C-0001"
    assert body["affected_mappings"][0]["framework_item_code"] == "PR.AA-01"
    assert body["affected_evidence"][0]["control_code"] == "C-0001"


async def test_changed_matched_text_is_visible_and_marks_its_controls_affected(
    client, db_session
):
    old_doc = await _document(db_session, "Password Policy v1", 1)
    old_clause = await _clause(db_session, old_doc.id, "4.2", "Rotate every 90 days.")
    control = await _control(db_session, "C-0001", old_clause.id)
    new_doc = await _document(db_session, "Password Policy v2", 2, supersedes_id=old_doc.id)
    await _clause(db_session, new_doc.id, "4.2", "Rotate every 180 days.")
    await _user(db_session)
    headers = await _auth(client)
    await db_session.flush()

    resp = await client.get(f"/api/documents/{new_doc.id}/change-impact", headers=headers)

    body = resp.json()
    assert body["matched"][0]["changed"] is True
    assert body["matched"][0]["old_text"] == "Rotate every 90 days."
    assert body["matched"][0]["text"] == "Rotate every 180 days."
    assert body["affected_controls"] == [
        {"id": control.id, "code": "C-0001", "title": "Control C-0001"}
    ]


async def test_report_warns_when_only_the_new_parser_found_clause_numbers(
    client, db_session
):
    old_doc = await _document(db_session, "Legacy", 1)
    old = await _clause(db_session, old_doc.id, "", "Old text")
    old.number = None
    new_doc = await _document(db_session, "Current", 2, supersedes_id=old_doc.id)
    await _clause(db_session, new_doc.id, "1", "New text")
    await _user(db_session)
    headers = await _auth(client)
    await db_session.flush()

    resp = await client.get(f"/api/documents/{new_doc.id}/change-impact", headers=headers)

    assert resp.json()["parser_generation_mismatch"] is True


async def test_clauses_merged_away_are_not_reported_as_removed_or_added(client, db_session):
    # 合并过的条款行不删（status='merged'，正文已并进胜者）。报告原来把它们也拿来配对：
    # 旧版里误切出来又并回去的「2a」会显示成「新版删掉了 2a」，读报告的人以为制度
    # 删了一段；新版里并掉的碎片则显示成「新增」。条款树不显示它们，报告也不该。
    old_doc = await _document(db_session, "Access Policy v1", 1)
    old_two = await _clause(db_session, old_doc.id, "2", "Review access.\n\nQuarterly.", 0)
    old_fragment = await _clause(db_session, old_doc.id, "2a", "Quarterly.", 1)
    old_fragment.status = "merged"
    old_fragment.merged_into_id = old_two.id
    new_doc = await _document(db_session, "Access Policy v2", 2, supersedes_id=old_doc.id)
    new_two = await _clause(db_session, new_doc.id, "2", "Review access.\n\nQuarterly.", 0)
    new_fragment = await _clause(db_session, new_doc.id, "2b", "Stray line.", 1)
    new_fragment.status = "merged"
    new_fragment.merged_into_id = new_two.id
    await _user(db_session)
    headers = await _auth(client)
    await db_session.flush()

    body = (await client.get(f"/api/documents/{new_doc.id}/change-impact", headers=headers)).json()

    assert body["removed"] == []
    assert body["added"] == []
    assert [(row["old_clause_id"], row["new_clause_id"], row["changed"]) for row in body["matched"]] == [
        (old_two.id, new_two.id, False),
    ]
