"""浏览器测试（`make e2e`）用的 RCSA 合成数据。只跑在隔离的 e2e 栈上，不进产品镜像的入口。

    python -m tests.e2e_rcsa_seed

控制点与映射**照样走确认队列**：先写成提案，再由管理员确认，由 materialize 落正式表
（铁律 1）。文档、条款、框架不在那条铁律里，直接建。机构名一律是虚构的 Acme。
已存在就跳过，可以重复执行。
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clauses.models import Clause
from app.controls.models import Control
from app.frameworks.models import Framework, FrameworkItem
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocStatus, DocType, Document
from app.rcsa.models import ProfileStatus
from app.rcsa.profiles import seed_default_profile
from app.review.materialize import update_control
from app.review.models import Proposal, ProposalKind
from app.review.service import Decision, decide

PASSWORD = "pw123456"
ACCOUNTS = (
    {"email": "rcsa-lead@example.com", "name": "Rhea Lead", "role": Role.GRC_LEAD},
    {"email": "rcsa-assessor@example.com", "name": "Asa Assessor", "role": Role.CONTRIBUTOR},
    {"email": "rcsa-reviewer@example.com", "name": "Remy Reviewer", "role": Role.GRC_LEAD},
)
CONTROLS = (
    ("Quarterly access review", "Access to production systems is reviewed every quarter.",
     "AC-2", "Accounts are reviewed every quarter by the system owner."),
    ("Backup restore test", "Backups of critical systems are restored and checked twice a year.",
     "CP-9", "Backups are restored and checked at least twice a year."),
)


async def seed(session: AsyncSession) -> None:
    if await session.scalar(select(Framework).where(Framework.key == "acme-rcsa")) is not None:
        return
    people = {}
    for account in ACCOUNTS:
        user = await session.scalar(select(User).where(User.email == account["email"]))
        if user is None:
            user = User(email=account["email"], name=account["name"], role=account["role"],
                        password_hash=hash_password(PASSWORD))
            session.add(user)
        people[account["email"]] = user
    await session.flush()
    lead = people["rcsa-lead@example.com"]

    framework = Framework(key="acme-rcsa", name_zh="", name_en="Acme IT Controls", version="1",
                          source="synthetic", item_count=len(CONTROLS))
    document = Document(
        title="Acme IT Security Policy", doc_type=DocType.POLICY, status=DocStatus.ACTIVE,
        file_hash="e2e0" * 16, file_path="/data/documents/e2e-acme-policy.txt",
        original_filename="acme-policy.txt", uploaded_by=lead.id,
    )
    session.add_all([framework, document])
    await session.flush()

    for index, (title, statement, item_code, item_text) in enumerate(CONTROLS, start=1):
        item = FrameworkItem(framework_id=framework.id, code=item_code, title=title, description=item_text,
                             level=1, order_index=index)
        clause = Clause(document_id=document.id, number=f"4.{index}", heading=title,
                        heading_path=f"Acme IT Security Policy › {title}", citation_label=f"4.{index}",
                        text=statement, order_index=index, level=1)
        session.add_all([item, clause])
        await session.flush()
        citation = [{"clause_id": clause.id, "quote": statement}]
        control_proposal = Proposal(
            kind=ProposalKind.CONTROL_EXTRACT, document_id=document.id, confidence=0.9, citations=citation,
            payload={"title": title, "statement": statement, "citations": citation},
        )
        session.add(control_proposal)
        await session.flush()
        await decide(session, control_proposal.id, actor=lead, decision=Decision.ACCEPT)
        control = await session.scalar(select(Control).where(Control.title == title))
        # 改负责人也走 materialize 的人工编辑入口，同样留审计。
        await update_control(session, control.id, {
            "owner_user_id": people["rcsa-assessor@example.com"].id,
            "expected": {"owner_user_id": control.owner_user_id},
        }, actor=lead)
        mapping_proposal = Proposal(
            kind=ProposalKind.MAPPING, confidence=0.9, citations=[],
            payload={"framework_item_id": item.id, "control_id": control.id, "strength": "full",
                     "framework_item_quote": item_text, "rationale": "The control does what the item asks."},
        )
        session.add(mapping_proposal)
        await session.flush()
        await decide(session, mapping_proposal.id, actor=lead, decision=Decision.ACCEPT)

    profile = await seed_default_profile(session, actor=lead)
    if profile.status == ProfileStatus.DRAFT:
        profile.definitions = profile.definitions | {
            "likelihood": [dict(level, criteria=f"Acme likelihood band {level['level']}")
                           for level in profile.definitions["likelihood"]],
            "impact_dimensions": ["service"],
            "impact": [dict(level, criteria={"service": f"Acme outage tier {level['level']}"})
                       for level in profile.definitions["impact"]],
        }
        profile.horizon = "12 months"
        profile.status = ProfileStatus.APPROVED
        profile.approved_by = lead.id
        profile.approved_at = datetime.now(UTC)
    await session.flush()


async def _main() -> None:
    import app.models  # noqa: F401 — 注册全部模型
    from app.db import session_factory

    async with session_factory() as session:
        await seed(session)
        await session.commit()
    print("RCSA e2e data ready")


if __name__ == "__main__":
    asyncio.run(_main())
