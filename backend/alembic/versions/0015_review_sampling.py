"""Add the continuous review sampling setting."""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO app_setting (key, value, created_at, updated_at)
        VALUES ('review_sample_rate', '{"value": 0.10}', now(), now())
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM app_setting WHERE key = 'review_sample_rate'")
