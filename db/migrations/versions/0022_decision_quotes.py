"""Feature 174: decision_quotes — the near-post (T−3 min) decision record, one row per race.

A frozen copy of the win/place quote netkeiba served ~3 minutes before post, plus the market-aware
predictions (mev-ens15-v1 / mev-binary-v2 / the 141 mixture) computed from that quote at that
moment, with the hashes needed to reproduce the row. One race has exactly one row for its lifetime
(UNIQUE(race_id)); the table is append-only at the database boundary (UPDATE/DELETE/TRUNCATE are
rejected by triggers), like attention_picks (0019) and purchase_records (0017). Settlement is not
stored: readers join official_win_payouts (0020) at read time.

Constitution V (1.0.1 note): this is a decision record (one frozen value per race per kind of
decision), not an odds history of race_horses.odds.

Revision ID: 0022_decision_quotes
Revises: 0021_win_odds_provenance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0022_decision_quotes"
down_revision = "0021_win_odds_provenance"
branch_labels = None
depends_on = None

TABLE = "decision_quotes"
_FN = """
CREATE OR REPLACE FUNCTION reject_decision_quote_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only; % is not allowed', TG_TABLE_NAME, TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "decision_quote_id",
            sa.Uuid(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("race_id", sa.String(length=12), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("official_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("post_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds_to_post", sa.Integer(), nullable=False),
        sa.Column("capture_target", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("field", JSONB(), nullable=False),
        sa.Column("pred", JSONB(), nullable=False),
        sa.Column("quote_digest", sa.Text(), nullable=False),
        sa.Column("field_digest", sa.Text(), nullable=False),
        sa.Column("features_sha256", sa.Text(), nullable=False),
        sa.Column("ensemble_version", sa.Text(), nullable=False),
        sa.Column("ensemble_manifest", sa.Text(), nullable=False),
        sa.Column("ensemble_manifest_sha256", sa.Text(), nullable=False),
        sa.Column("single_version", sa.Text(), nullable=False),
        sa.Column("single_booster", sa.Text(), nullable=False),
        sa.Column("single_booster_sha256", sa.Text(), nullable=False),
        sa.Column("mix_version", sa.Text(), nullable=True),
        sa.Column("mix_status", sa.Text(), nullable=False),
        sa.Column("mix_coefficient_year", sa.Integer(), nullable=True),
        sa.Column("logic_version", sa.Text(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("race_id", name="uq_decision_quotes_race_id"),
        sa.CheckConstraint("seconds_to_post >= 0", name="ck_decision_quotes_seconds_to_post"),
        sa.CheckConstraint("n >= 2", name="ck_decision_quotes_n"),
    )
    op.create_index("ix_decision_quotes_captured_at", TABLE, ["captured_at"])
    op.execute(_FN)
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_reject_mutation BEFORE UPDATE OR DELETE ON {TABLE} "
        "FOR EACH ROW EXECUTE FUNCTION reject_decision_quote_mutation();"
    )
    op.execute(
        f"CREATE TRIGGER trg_{TABLE}_reject_truncate BEFORE TRUNCATE ON {TABLE} "
        "FOR EACH STATEMENT EXECUTE FUNCTION reject_decision_quote_mutation();"
    )
    op.execute(f"REVOKE TRUNCATE ON {TABLE} FROM PUBLIC;")


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_{TABLE}_reject_truncate ON {TABLE};")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{TABLE}_reject_mutation ON {TABLE};")
    op.execute("DROP FUNCTION IF EXISTS reject_decision_quote_mutation();")
    op.drop_index("ix_decision_quotes_captured_at", table_name=TABLE)
    op.drop_table(TABLE)
