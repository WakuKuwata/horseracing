"""attention_race_scans / attention_picks / attention_checkpoints (Feature 138)

Revision ID: 0019_attention_picks
Revises: 0018_market_ev_predictions
Create Date: 2026-10-02

Feature 138 freezes five "attention conditions" (S1-S5) and verifies them prospectively. The
prospective record must not be editable after the fact (that is the whole point of a forward
test), so all three tables are append-only at the database boundary: UPDATE, DELETE and TRUNCATE
are rejected by triggers sharing one function (0017 precedent; the TRUNCATE trigger also stops the
table owner, whom a plain REVOKE would not).

* attention_race_scans — the FIRST ensemble computation of a race. Whether a run can insert this
  row (INSERT ... ON CONFLICT DO NOTHING RETURNING) decides whether it may create picks, so a race
  that had no qualifying horse at its first computation never gets a pick later (plan D16).
* attention_picks — one ``kind='pick'`` row per (race, horse, rule, rule-set version) for life,
  frozen with the values used; ``kind='void'`` rows are appended when the picked horse is
  scratched (one void per pick).
* attention_checkpoints — the recorded 300/600-point decision of a rule; the stage follows the
  record so a late result correction cannot flip it (plan D18).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_attention_picks"
down_revision = "0018_market_ev_predictions"
branch_labels = None
depends_on = None

# Literal, not imported: a migration is a historical record (0015 precedent).
_RULE_IDS = "('S1', 'S2', 'S3', 'S4', 'S5')"
_TABLES = ("attention_race_scans", "attention_picks", "attention_checkpoints")

_REJECT_FN = """
CREATE OR REPLACE FUNCTION reject_attention_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not allowed', TG_TABLE_NAME, TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""
_DROP_FN = "DROP FUNCTION IF EXISTS reject_attention_mutation();"


def _create_triggers(table: str) -> None:
    op.execute(
        f"CREATE TRIGGER trg_{table}_reject_mutation BEFORE UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_attention_mutation();"
    )
    op.execute(
        f"CREATE TRIGGER trg_{table}_reject_truncate BEFORE TRUNCATE ON {table} "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_attention_mutation();"
    )
    # Belt & braces for non-owner roles; the TRUNCATE trigger covers the owner.
    op.execute(f"REVOKE TRUNCATE ON {table} FROM PUBLIC;")


def _drop_triggers(table: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_mutation ON {table};")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_reject_truncate ON {table};")


def upgrade() -> None:
    op.create_table(
        "attention_race_scans",
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("post_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_pending_at_compute", sa.Boolean(), nullable=False),
        sa.Column("field_digest", sa.Text(), nullable=False),
        sa.Column("n_picks", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "race_id", "rule_set_version", name=op.f("pk_attention_race_scans")
        ),
        sa.CheckConstraint("n_picks >= 0", name=op.f("ck_attention_race_scans_n_picks")),
    )

    op.create_table(
        "attention_picks",
        sa.Column("pick_id", sa.Uuid(), nullable=False),
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("horse_id", sa.Text(), nullable=False),
        sa.Column("horse_number", sa.Integer(), nullable=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column(
            "voids_pick_id", sa.Uuid(), sa.ForeignKey("attention_picks.pick_id"), nullable=True
        ),
        sa.Column("ens_expected_return", sa.Numeric(), nullable=True),
        sa.Column("single_expected_return", sa.Numeric(), nullable=True),
        sa.Column("odds_used", sa.Numeric(), nullable=True),
        sa.Column("odds_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("days_since_last", sa.Numeric(), nullable=True),
        sa.Column("post_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seconds_to_post", sa.Integer(), nullable=True),
        sa.Column("result_pending_at_compute", sa.Boolean(), nullable=False),
        sa.Column("field_digest", sa.Text(), nullable=False),
        sa.Column("ensemble_model_version", sa.Text(), nullable=False),
        sa.Column("single_model_version", sa.Text(), nullable=False),
        sa.Column("logic_version", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("selection_policy_version", sa.Text(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("pick_id", name=op.f("pk_attention_picks")),
        sa.CheckConstraint(f"rule_id IN {_RULE_IDS}", name=op.f("ck_attention_picks_rule_id")),
        sa.CheckConstraint("kind IN ('pick', 'void')", name=op.f("ck_attention_picks_kind")),
        sa.CheckConstraint(
            "(kind = 'void') = (void_reason IS NOT NULL)",
            name=op.f("ck_attention_picks_void_reason_present"),
        ),
        sa.CheckConstraint(
            "void_reason IS NULL OR void_reason IN ('scratched')",
            name=op.f("ck_attention_picks_void_reason"),
        ),
        sa.CheckConstraint(
            "(kind = 'void') = (voids_pick_id IS NOT NULL)",
            name=op.f("ck_attention_picks_void_target"),
        ),
        sa.CheckConstraint(
            "kind <> 'pick' OR (ens_expected_return IS NOT NULL AND odds_used >= 1.0 "
            "AND odds_observed_at IS NOT NULL AND horse_number IS NOT NULL)",
            name=op.f("ck_attention_picks_pick_values"),
        ),
    )
    op.create_index(
        "uq_attention_picks_pick",
        "attention_picks",
        ["race_id", "horse_id", "rule_id", "rule_set_version"],
        unique=True,
        postgresql_where=sa.text("kind = 'pick'"),
    )
    op.create_index(
        "uq_attention_picks_void",
        "attention_picks",
        ["voids_pick_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'void'"),
    )
    op.create_index("ix_attention_picks_race", "attention_picks", ["race_id"])
    op.create_index(
        "ix_attention_picks_rule_computed", "attention_picks", ["rule_id", "computed_at"]
    )

    op.create_table(
        "attention_checkpoints",
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("checkpoint", sa.Integer(), nullable=False),
        sa.Column("selection_policy_version", sa.Text(), nullable=False),
        sa.Column("rule_set_version", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("n_counted", sa.Integer(), nullable=False),
        sa.Column("n_hits", sa.Integer(), nullable=False),
        sa.Column("roi_frozen", sa.Numeric(), nullable=False),
        sa.Column("ci_low", sa.Numeric(), nullable=True),
        sa.Column("ci_high", sa.Numeric(), nullable=True),
        sa.Column("bootstrap", postgresql.JSONB(), nullable=False),
        sa.Column("counted_pick_ids_sha256", sa.Text(), nullable=False),
        sa.Column(
            "last_pick_id", sa.Uuid(), sa.ForeignKey("attention_picks.pick_id"), nullable=False
        ),
        sa.Column("settlement_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prospective_start_date", sa.Date(), nullable=False),
        sa.Column("skipped_pending_before_last", sa.Integer(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("checkpoint_id", name=op.f("pk_attention_checkpoints")),
        sa.CheckConstraint(
            f"rule_id IN {_RULE_IDS}", name=op.f("ck_attention_checkpoints_rule_id")
        ),
        sa.CheckConstraint(
            "checkpoint IN (300, 600)", name=op.f("ck_attention_checkpoints_checkpoint")
        ),
        sa.CheckConstraint(
            "decision IN ('passed', 'failed', 'continue', 'undecided')",
            name=op.f("ck_attention_checkpoints_decision"),
        ),
        sa.CheckConstraint(
            "(checkpoint = 300 AND decision <> 'undecided') "
            "OR (checkpoint = 600 AND decision <> 'continue')",
            name=op.f("ck_attention_checkpoints_decision_by_checkpoint"),
        ),
        sa.CheckConstraint(
            "n_counted = checkpoint", name=op.f("ck_attention_checkpoints_n_counted")
        ),
        sa.UniqueConstraint(
            "rule_id",
            "checkpoint",
            "selection_policy_version",
            "rule_set_version",
            name="uq_attention_checkpoints_decision",
        ),
    )
    op.create_index("ix_attention_checkpoints_rule", "attention_checkpoints", ["rule_id"])

    op.execute(_REJECT_FN)
    for table in _TABLES:
        _create_triggers(table)


def downgrade() -> None:
    for table in _TABLES:
        _drop_triggers(table)
    op.drop_index("ix_attention_checkpoints_rule", table_name="attention_checkpoints")
    op.drop_table("attention_checkpoints")
    op.drop_index("ix_attention_picks_rule_computed", table_name="attention_picks")
    op.drop_index("ix_attention_picks_race", table_name="attention_picks")
    op.drop_index("uq_attention_picks_void", table_name="attention_picks")
    op.drop_index("uq_attention_picks_pick", table_name="attention_picks")
    op.drop_table("attention_picks")
    op.drop_table("attention_race_scans")
    op.execute(_DROP_FN)
