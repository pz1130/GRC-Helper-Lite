"""RCSA: allow the rcsa_prefill proposal kind."""

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

KINDS = (
    "'control_extract','mapping','relation','conflict','answer','maturity_score',"
    "'evidence_suggestion','audit_prediction','matrix_mapping'"
)


def upgrade() -> None:
    op.drop_constraint("proposal_kind", "proposals", type_="check")
    op.create_check_constraint("proposal_kind", "proposals", f"kind IN ({KINDS},'rcsa_prefill')")


def downgrade() -> None:
    # 有预填建议时拒绝回退：删掉它们会让审计里的 proposal_id 指向空气。
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM proposals WHERE kind = 'rcsa_prefill')")):
        raise RuntimeError("RCSA prefill suggestions exist; disable the channel and fix forward instead")
    op.drop_constraint("proposal_kind", "proposals", type_="check")
    op.create_check_constraint("proposal_kind", "proposals", f"kind IN ({KINDS})")
