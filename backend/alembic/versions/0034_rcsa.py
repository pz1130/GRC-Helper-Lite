"""RCSA: ten tables, register source, idempotency index, and freeze triggers."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

RCSA_TABLES = (
    "rcsa_reviews",
    "rcsa_trigger_controls",
    "rcsa_triggers",
    "rcsa_actions",
    "rcsa_item_risks",
    "rcsa_response_evidence",
    "rcsa_responses",
    "rcsa_items",
    "rcsa_campaigns",
    "rcsa_scoring_profiles",
)

OLD_RISK_SOURCES = "'gap','conflict','manual','audit_finding'"


# 冻结规则放在数据库里：服务层写错、脚本直改都绕不过去。错误码 23000 让驱动
# 把它报成 IntegrityError，与约束失败同一类处理。asyncpg 一次只收一条语句，所以逐条执行。
FREEZE_TRIGGERS = (
    """
    CREATE FUNCTION rcsa_responses_freeze() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'rcsa response % is submitted and cannot be deleted', OLD.id
                    USING ERRCODE = '23000';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status <> 'draft' AND (
            NEW.status = 'draft'
            OR NEW.item_id <> OLD.item_id
            OR NEW.revision <> OLD.revision
            OR NEW.answer_schema <> OLD.answer_schema
            OR NEW.answer IS DISTINCT FROM OLD.answer
            OR NEW.risk_snapshot IS DISTINCT FROM OLD.risk_snapshot
            OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by
            OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at
        ) THEN
            RAISE EXCEPTION 'rcsa response % is submitted and frozen', OLD.id USING ERRCODE = '23000';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER rcsa_responses_freeze BEFORE UPDATE OR DELETE ON rcsa_responses
        FOR EACH ROW EXECUTE FUNCTION rcsa_responses_freeze()
    """,
    """
    CREATE FUNCTION rcsa_response_evidence_freeze() RETURNS trigger AS $$
    DECLARE
        response_status text;
    BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT status INTO response_status FROM rcsa_responses WHERE id = OLD.response_id;
            IF response_status <> 'draft' THEN
                RAISE EXCEPTION 'evidence of submitted rcsa response % is frozen', OLD.response_id
                    USING ERRCODE = '23000';
            END IF;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            SELECT status INTO response_status FROM rcsa_responses WHERE id = NEW.response_id;
            IF response_status <> 'draft' THEN
                RAISE EXCEPTION 'evidence of submitted rcsa response % is frozen', NEW.response_id
                    USING ERRCODE = '23000';
            END IF;
            RETURN NEW;
        END IF;
        RETURN OLD;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER rcsa_response_evidence_freeze BEFORE INSERT OR UPDATE OR DELETE ON rcsa_response_evidence
        FOR EACH ROW EXECUTE FUNCTION rcsa_response_evidence_freeze()
    """,
    """
    CREATE FUNCTION rcsa_reviews_append_only() RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'rcsa reviews are append-only' USING ERRCODE = '23000';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER rcsa_reviews_append_only BEFORE UPDATE OR DELETE ON rcsa_reviews
        FOR EACH ROW EXECUTE FUNCTION rcsa_reviews_append_only()
    """,
    """
    CREATE FUNCTION rcsa_campaigns_frozen() RETURNS trigger AS $$
    BEGIN
        IF OLD.status IN ('closed', 'cancelled') THEN
            RAISE EXCEPTION 'rcsa campaign % is % and read-only', OLD.id, OLD.status
                USING ERRCODE = '23000';
        END IF;
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER rcsa_campaigns_frozen BEFORE UPDATE OR DELETE ON rcsa_campaigns
        FOR EACH ROW EXECUTE FUNCTION rcsa_campaigns_frozen()
    """,
)

def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def _user_fk(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=nullable)


def _audited() -> list[sa.Column]:
    return [
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _user_fk("created_by", nullable=False),
        _user_fk("updated_by", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.drop_constraint("risk_source", "risk_entries", type_="check")
    op.create_check_constraint("risk_source", "risk_entries", f"source IN ({OLD_RISK_SOURCES},'rcsa')")
    op.create_index(
        "uq_risk_entries_rcsa_item_risk",
        "risk_entries",
        [sa.text("(source_ref ->> 'rcsa_item_risk_id')")],
        unique=True,
        postgresql_where=sa.text("source = 'rcsa'"),
    )
    op.create_index(
        "uq_audit_log_rcsa_command",
        "audit_log",
        [sa.text("(after ->> 'command_key')")],
        unique=True,
        postgresql_where=sa.text("action LIKE 'rcsa.%' AND (after ->> 'command_key') IS NOT NULL"),
    )

    op.create_table(
        "rcsa_scoring_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("algorithm", _enum("product", name="rcsa_scoring_algorithm"), nullable=False),
        sa.Column("definitions", postgresql.JSONB(), nullable=False),
        sa.Column("bands", postgresql.JSONB(), nullable=False),
        sa.Column("horizon", sa.String(200), nullable=False),
        sa.Column("status", _enum("draft", "approved", name="rcsa_profile_status"), nullable=False),
        _user_fk("approved_by"),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        *_audited(),
        sa.UniqueConstraint("key", "revision", name="uq_rcsa_profile_revision"),
        sa.CheckConstraint(
            "status <> 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL"
            " AND length(trim(horizon)) > 0)",
            name="ck_rcsa_profile_approved",
        ),
    )

    op.create_table(
        "rcsa_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("framework_id", sa.Integer(), sa.ForeignKey("frameworks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("framework_snapshot", postgresql.JSONB()),
        sa.Column(
            "profile_id", sa.Integer(), sa.ForeignKey("rcsa_scoring_profiles.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("profile_snapshot", postgresql.JSONB()),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("due_at", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        _user_fk("owner_id", nullable=False),
        sa.Column(
            "status",
            _enum("draft", "active", "in_review", "closed", "cancelled", name="rcsa_campaign_status"),
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "predecessor_campaign_id", sa.Integer(), sa.ForeignKey("rcsa_campaigns.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "audit_engagement_id", sa.Integer(), sa.ForeignKey("audit_engagements.id", ondelete="RESTRICT")
        ),
        _user_fk("activated_by"),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("closed_snapshot", postgresql.JSONB()),
        _user_fk("closed_by"),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        *_audited(),
        sa.CheckConstraint("period_start <= period_end", name="ck_rcsa_campaign_period"),
        sa.CheckConstraint(
            "status IN ('draft', 'cancelled')"
            " OR (framework_snapshot IS NOT NULL AND profile_snapshot IS NOT NULL)",
            name="ck_rcsa_campaign_frozen",
        ),
        sa.CheckConstraint(
            "status <> 'closed' OR (closed_snapshot IS NOT NULL AND closed_by IS NOT NULL"
            " AND closed_at IS NOT NULL)",
            name="ck_rcsa_campaign_closed",
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' OR length(trim(status_reason)) > 0",
            name="ck_rcsa_campaign_cancel_reason",
        ),
    )
    op.create_index("ix_rcsa_campaigns_status", "rcsa_campaigns", ["status"])
    op.create_index("ix_rcsa_campaigns_framework_id", "rcsa_campaigns", ["framework_id"])
    op.create_index("ix_rcsa_campaigns_profile_id", "rcsa_campaigns", ["profile_id"])
    op.create_index("ix_rcsa_campaigns_owner_id", "rcsa_campaigns", ["owner_id"])
    op.create_index("ix_rcsa_campaigns_predecessor_campaign_id", "rcsa_campaigns", ["predecessor_campaign_id"])
    op.create_index("ix_rcsa_campaigns_audit_engagement_id", "rcsa_campaigns", ["audit_engagement_id"])

    op.create_table(
        "rcsa_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("rcsa_campaigns.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("control_snapshot", postgresql.JSONB()),
        sa.Column("mapping_snapshot", postgresql.JSONB()),
        sa.Column("source_snapshot", postgresql.JSONB()),
        _user_fk("assignee_id"),
        _user_fk("reviewer_id"),
        sa.Column(
            "status",
            _enum("pending", "draft", "submitted", "returned", "approved", "excluded", name="rcsa_item_status"),
            nullable=False,
        ),
        sa.Column("current_response_id", sa.Integer()),
        sa.Column(
            "provider_type",
            _enum("internal", "third_party", name="rcsa_provider_type"),
            nullable=False,
            server_default="internal",
        ),
        sa.Column("service_ref", sa.String(300)),
        sa.Column("exit_ref", sa.String(300)),
        *_audited(),
        sa.UniqueConstraint("campaign_id", "control_id", name="uq_rcsa_item_control"),
        sa.CheckConstraint(
            "assignee_id IS NULL OR reviewer_id IS NULL OR assignee_id <> reviewer_id",
            name="ck_rcsa_item_reviewer_not_assignee",
        ),
    )
    op.create_index("ix_rcsa_items_campaign_status", "rcsa_items", ["campaign_id", "status"])
    op.create_index("ix_rcsa_items_assignee_status", "rcsa_items", ["assignee_id", "status"])
    op.create_index("ix_rcsa_items_reviewer_status", "rcsa_items", ["reviewer_id", "status"])
    op.create_index("ix_rcsa_items_control_id", "rcsa_items", ["control_id"])

    op.create_table(
        "rcsa_responses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("rcsa_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "status", _enum("draft", "submitted", "approved", "returned", name="rcsa_response_status"), nullable=False
        ),
        sa.Column("answer_schema", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("answer", postgresql.JSONB(), nullable=False),
        sa.Column("risk_snapshot", postgresql.JSONB()),
        _user_fk("submitted_by"),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        *_audited(),
        sa.UniqueConstraint("item_id", "revision", name="uq_rcsa_response_revision"),
        sa.UniqueConstraint("id", "item_id", name="uq_rcsa_response_item"),
        sa.CheckConstraint(
            "status = 'draft' OR (submitted_by IS NOT NULL AND submitted_at IS NOT NULL"
            " AND risk_snapshot IS NOT NULL)",
            name="ck_rcsa_response_submitted",
        ),
        sa.CheckConstraint("answer_schema = 1", name="ck_rcsa_response_answer_schema"),
    )
    op.create_index(
        "uq_rcsa_response_one_draft",
        "rcsa_responses",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    # 两表互指：items 先建，当前版本的复合外键等 responses 建好再补。
    op.create_foreign_key(
        "fk_rcsa_items_current_response",
        "rcsa_items",
        "rcsa_responses",
        ["current_response_id", "id"],
        ["id", "item_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "rcsa_response_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "response_id", sa.Integer(), sa.ForeignKey("rcsa_responses.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("evidence_item_id", sa.Integer(), sa.ForeignKey("evidence_items.id", ondelete="RESTRICT")),
        sa.Column("external_location", sa.Text()),
        sa.Column(
            "purpose", _enum("self_assessment", "independent_test", name="rcsa_evidence_purpose"), nullable=False
        ),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        _user_fk("tester_user_id"),
        sa.Column("tester_name", sa.String(200)),
        sa.Column("tested_at", sa.Date()),
        sa.Column("test_scope", sa.Text(), nullable=False, server_default=""),
        sa.Column("conclusion", sa.Text(), nullable=False, server_default=""),
        sa.Column("independence_basis", sa.Text(), nullable=False, server_default=""),
        _user_fk("created_by", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "evidence_item_id IS NOT NULL OR length(trim(coalesce(external_location, ''))) > 0",
            name="ck_rcsa_evidence_reference",
        ),
        sa.CheckConstraint(
            "purpose <> 'independent_test' OR ("
            "(tester_user_id IS NOT NULL OR length(trim(coalesce(tester_name, ''))) > 0)"
            " AND tested_at IS NOT NULL"
            " AND length(trim(test_scope)) > 0"
            " AND length(trim(conclusion)) > 0"
            " AND length(trim(independence_basis)) > 0)",
            name="ck_rcsa_evidence_independent_test",
        ),
    )
    op.create_index("ix_rcsa_response_evidence_response_id", "rcsa_response_evidence", ["response_id"])
    op.create_index("ix_rcsa_response_evidence_evidence_item_id", "rcsa_response_evidence", ["evidence_item_id"])

    op.create_table(
        "rcsa_item_risks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("rcsa_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scenario_key", sa.String(64), nullable=False),
        sa.Column("register_risk_id", sa.Integer(), sa.ForeignKey("risk_entries.id", ondelete="RESTRICT")),
        sa.Column("cause", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("inherent_likelihood", sa.Integer(), nullable=False),
        sa.Column("inherent_impact", sa.Integer(), nullable=False),
        sa.Column("inherent_score", sa.Integer(), nullable=False),
        sa.Column("inherent_rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("residual_likelihood", sa.Integer()),
        sa.Column("residual_impact", sa.Integer()),
        sa.Column("residual_score", sa.Integer()),
        sa.Column("residual_rationale", sa.Text(), nullable=False, server_default=""),
        _user_fk("owner_id", nullable=False),
        sa.Column(
            "disposition", _enum("accept", "reduce", "transfer", "avoid", "terminate", name="rcsa_disposition")
        ),
        sa.Column(
            "disposition_status",
            _enum("pending", "proposed", "approved", "returned", name="rcsa_disposition_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("disposition_revision", sa.Integer(), nullable=False, server_default="0"),
        _user_fk("disposition_proposed_by"),
        sa.Column("disposition_proposed_at", sa.DateTime(timezone=True)),
        sa.Column("accept_until", sa.Date()),
        sa.Column("reassess_at", sa.Date()),
        sa.Column("disposition_details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        *_audited(),
        sa.UniqueConstraint("item_id", "scenario_key", name="uq_rcsa_item_risk_scenario"),
        sa.CheckConstraint(
            "inherent_likelihood BETWEEN 1 AND 5 AND inherent_impact BETWEEN 1 AND 5"
            " AND inherent_score BETWEEN 1 AND 25",
            name="ck_rcsa_risk_inherent",
        ),
        sa.CheckConstraint(
            "(residual_likelihood IS NULL) = (residual_impact IS NULL)"
            " AND (residual_likelihood IS NULL) = (residual_score IS NULL)",
            name="ck_rcsa_risk_residual_pair",
        ),
        sa.CheckConstraint(
            "residual_likelihood IS NULL OR (residual_likelihood BETWEEN 1 AND 5"
            " AND residual_impact BETWEEN 1 AND 5 AND residual_score BETWEEN 1 AND 25)",
            name="ck_rcsa_risk_residual",
        ),
        sa.CheckConstraint(
            "disposition_status = 'pending' OR (disposition IS NOT NULL"
            " AND disposition_proposed_by IS NOT NULL AND disposition_proposed_at IS NOT NULL)",
            name="ck_rcsa_risk_disposition_proposed",
        ),
        sa.CheckConstraint(
            "disposition IS DISTINCT FROM 'accept' OR disposition_status = 'pending'"
            " OR (accept_until IS NOT NULL AND reassess_at IS NOT NULL)",
            name="ck_rcsa_risk_accept_dates",
        ),
    )
    op.create_index("ix_rcsa_item_risks_register_risk_id", "rcsa_item_risks", ["register_risk_id"])
    op.create_index("ix_rcsa_item_risks_owner_id", "rcsa_item_risks", ["owner_id"])
    op.create_index(
        "ix_rcsa_item_risks_accept_until",
        "rcsa_item_risks",
        ["accept_until"],
        postgresql_where=sa.text("disposition = 'accept'"),
    )

    op.create_table(
        "rcsa_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "item_risk_id", sa.Integer(), sa.ForeignKey("rcsa_item_risks.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("root_cause", sa.Text(), nullable=False, server_default=""),
        sa.Column("measure", sa.Text(), nullable=False, server_default=""),
        _user_fk("owner_id", nullable=False),
        _user_fk("verifier_id", nullable=False),
        sa.Column("due_at", sa.Date(), nullable=False),
        sa.Column("success_criteria", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "open", "in_progress", "pending_verification", "verified", "cancelled", name="rcsa_action_status"
            ),
            nullable=False,
        ),
        sa.Column(
            "implementation_evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("verification_result", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        *_audited(),
        sa.CheckConstraint("owner_id <> verifier_id", name="ck_rcsa_action_verifier_not_owner"),
        sa.CheckConstraint("status <> 'verified' OR verified_at IS NOT NULL", name="ck_rcsa_action_verified_at"),
    )
    op.create_index("ix_rcsa_actions_item_risk_id", "rcsa_actions", ["item_risk_id"])
    op.create_index("ix_rcsa_actions_owner_due", "rcsa_actions", ["owner_id", "due_at"])
    op.create_index("ix_rcsa_actions_verifier_status", "rcsa_actions", ["verifier_id", "status"])

    op.create_table(
        "rcsa_triggers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "trigger_type", _enum("planned_change", "incident", "other", name="rcsa_trigger_type"), nullable=False
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_ref", sa.String(500), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_effective_at", sa.DateTime(timezone=True)),
        _user_fk("owner_id", nullable=False),
        sa.Column("decision_due_at", sa.Date(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "registered", "triaged", "awaiting_assessment", "no_reassessment", "completed",
                name="rcsa_trigger_status",
            ),
            nullable=False,
        ),
        sa.Column("disposition", _enum("reassess", "no_reassessment", name="rcsa_trigger_disposition")),
        _user_fk("proposed_by"),
        sa.Column("proposed_at", sa.DateTime(timezone=True)),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("rcsa_campaigns.id", ondelete="RESTRICT")),
        *_audited(),
        sa.CheckConstraint(
            "trigger_type <> 'planned_change' OR planned_effective_at IS NOT NULL",
            name="ck_rcsa_trigger_planned_date",
        ),
        sa.CheckConstraint(
            "status <> 'awaiting_assessment' OR disposition = 'reassess'", name="ck_rcsa_trigger_awaiting"
        ),
        sa.CheckConstraint(
            "status <> 'no_reassessment' OR (disposition = 'no_reassessment'"
            " AND proposed_by IS NOT NULL AND proposed_at IS NOT NULL"
            " AND length(trim(rationale)) > 0)",
            name="ck_rcsa_trigger_no_reassessment",
        ),
    )
    op.create_index("ix_rcsa_triggers_owner_due", "rcsa_triggers", ["owner_id", "decision_due_at"])
    op.create_index("ix_rcsa_triggers_status", "rcsa_triggers", ["status"])
    op.create_index("ix_rcsa_triggers_campaign_id", "rcsa_triggers", ["campaign_id"])

    op.create_table(
        "rcsa_trigger_controls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trigger_id", sa.Integer(), sa.ForeignKey("rcsa_triggers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("control_id", sa.Integer(), sa.ForeignKey("controls.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("control_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "coverage_disposition",
            _enum("pending", "assess", "excluded", name="rcsa_coverage_disposition"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("coverage_reason", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("trigger_id", "control_id", name="uq_rcsa_trigger_control"),
        sa.CheckConstraint(
            "coverage_disposition <> 'excluded' OR length(trim(coverage_reason)) > 0",
            name="ck_rcsa_trigger_control_excluded_reason",
        ),
    )
    op.create_index("ix_rcsa_trigger_controls_control_id", "rcsa_trigger_controls", ["control_id"])

    op.create_table(
        "rcsa_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("response_id", sa.Integer(), sa.ForeignKey("rcsa_responses.id", ondelete="RESTRICT")),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("rcsa_items.id", ondelete="RESTRICT")),
        sa.Column("item_risk_id", sa.Integer(), sa.ForeignKey("rcsa_item_risks.id", ondelete="RESTRICT")),
        sa.Column("action_id", sa.Integer(), sa.ForeignKey("rcsa_actions.id", ondelete="RESTRICT")),
        sa.Column("trigger_id", sa.Integer(), sa.ForeignKey("rcsa_triggers.id", ondelete="RESTRICT")),
        sa.Column(
            "decision",
            _enum(
                "approve", "return", "exclude", "approve_disposition", "return_disposition",
                "verify_pass", "verify_fail", "approve_cancellation",
                "approve_no_reassessment", "reject_no_reassessment",
                name="rcsa_review_decision",
            ),
            nullable=False,
        ),
        sa.Column("target_version", sa.Integer(), nullable=False),
        _user_fk("reviewer_id", nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "num_nonnulls(response_id, item_id, item_risk_id, action_id, trigger_id) = 1",
            name="ck_rcsa_review_one_target",
        ),
        sa.CheckConstraint(
            "(decision IN ('approve', 'return') AND response_id IS NOT NULL)"
            " OR (decision = 'exclude' AND item_id IS NOT NULL)"
            " OR (decision IN ('approve_disposition', 'return_disposition') AND item_risk_id IS NOT NULL)"
            " OR (decision IN ('verify_pass', 'verify_fail', 'approve_cancellation')"
            " AND action_id IS NOT NULL)"
            " OR (decision IN ('approve_no_reassessment', 'reject_no_reassessment')"
            " AND trigger_id IS NOT NULL)",
            name="ck_rcsa_review_decision_target",
        ),
        sa.CheckConstraint(
            "decision NOT IN ('return', 'exclude', 'return_disposition', 'verify_fail',"
            " 'approve_cancellation', 'reject_no_reassessment') OR length(trim(reason)) > 0",
            name="ck_rcsa_review_reason",
        ),
    )
    for column in ("response_id", "item_id", "item_risk_id", "action_id", "trigger_id"):
        op.create_index(f"ix_rcsa_reviews_{column}", "rcsa_reviews", [column])

    for statement in FREEZE_TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    # 有 RCSA 业务数据时拒绝回退：降级会删表，评估记录与审计引用的对象会一起消失。
    connection = op.get_bind()
    in_use = [
        table for table in RCSA_TABLES if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})"))
    ]
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM risk_entries WHERE source = 'rcsa')")):
        in_use.append("risk_entries(source=rcsa)")
    if in_use:
        raise RuntimeError(
            "RCSA data exists in " + ", ".join(in_use) + "; disable RCSA and fix forward instead of downgrading"
        )

    for table, function in (
        ("rcsa_campaigns", "rcsa_campaigns_frozen"),
        ("rcsa_reviews", "rcsa_reviews_append_only"),
        ("rcsa_response_evidence", "rcsa_response_evidence_freeze"),
        ("rcsa_responses", "rcsa_responses_freeze"),
    ):
        op.execute(f"DROP TRIGGER {function} ON {table}")
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_constraint("fk_rcsa_items_current_response", "rcsa_items", type_="foreignkey")
    for table in RCSA_TABLES:
        op.drop_table(table)
    op.drop_index("uq_audit_log_rcsa_command", table_name="audit_log")
    op.drop_index("uq_risk_entries_rcsa_item_risk", table_name="risk_entries")
    op.drop_constraint("risk_source", "risk_entries", type_="check")
    op.create_check_constraint("risk_source", "risk_entries", f"source IN ({OLD_RISK_SOURCES})")
