"""purchase_records — the user's ACTUAL bets, append-only at the database boundary (Feature 106)

Revision ID: 0017_purchase_records
Revises: 0016_throttle_key_merge
Create Date: 2026-08-31

Every settlement view so far answered "what would the POLICY have returned"; nothing could answer
"what did the USER actually lose". This table records the user's real actions (buy-as-presented /
modified / skipped / freeform), freezing the presented slip (selections, integer stakes, the
frozen odds and the prediction_run that rendered it) INTO the record, because the stake amounts
derive from a budget that lives only in the front-end's localStorage — the server cannot
reproduce the presentation after the fact (research D2).

Append-only is enforced HERE, not as an app-layer convention: preventing self-deception (deleting
a losing record) is the point of the feature, so UPDATE, DELETE *and TRUNCATE* are rejected by
triggers (TRUNCATE via a statement-level trigger — the local runtime connects as the table owner,
whom a plain REVOKE would not stop). Corrections/voids are NEW rows referencing the original
(research D6, chaos_readouts precedent 084).

`result_pending_at_record` stores the OBSERVED fact "no race_results row existed when this was
recorded" — deliberately not named "pre_race": absence of results proves only that ingestion has
not happened yet (codex Q4, research D5). `pending_basis_at` is when that observation was made.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_purchase_records"
down_revision = "0016_throttle_key_merge"
branch_labels = None
depends_on = None

# Literal, not imported: a migration is a historical record (0015 precedent).
_KINDS = (
    "as_presented", "modified", "skipped_presented", "no_recommendation",
    "presentation_unavailable", "freeform", "correction", "void",
)

_REJECT_FN = """
CREATE OR REPLACE FUNCTION reject_purchase_record_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'purchase_records is append-only; % is not allowed (corrections are new rows)',
        TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
"""

_CREATE_ROW_TRIGGER = """
CREATE TRIGGER trg_purchase_records_reject_mutation
BEFORE UPDATE OR DELETE ON purchase_records
FOR EACH ROW EXECUTE FUNCTION reject_purchase_record_mutation();
"""

_CREATE_TRUNCATE_TRIGGER = """
CREATE TRIGGER trg_purchase_records_reject_truncate
BEFORE TRUNCATE ON purchase_records
FOR EACH STATEMENT EXECUTE FUNCTION reject_purchase_record_mutation();
"""

_DROP_TRIGGERS = """
DROP TRIGGER IF EXISTS trg_purchase_records_reject_mutation ON purchase_records;
DROP TRIGGER IF EXISTS trg_purchase_records_reject_truncate ON purchase_records;
"""

_DROP_FN = "DROP FUNCTION IF EXISTS reject_purchase_record_mutation();"


def upgrade() -> None:
    op.create_table(
        "purchase_records",
        sa.Column("purchase_record_id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        # actual bets: [{bet_type, selection, amount_yen, odds_used?}] — [] for skips/void
        sa.Column("bets", postgresql.JSONB(), nullable=False),
        # the slip as rendered (selections, integer stakes, frozen odds value/source/time,
        # win_policy, snapshot_schema_version). NULL for correction/void (they REFERENCE the
        # original instead of copying it — codex Q2), freeform and presentation_unavailable.
        sa.Column("presented_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("prediction_run_id", sa.Uuid(),
                  sa.ForeignKey("prediction_runs.prediction_run_id"), nullable=True),
        sa.Column("corrects_record_id", sa.Uuid(),
                  sa.ForeignKey("purchase_records.purchase_record_id"), nullable=True),
        sa.Column("result_pending_at_record", sa.Boolean(), nullable=False),
        sa.Column("pending_basis_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_request_id", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in _KINDS)),
            name="ck_purchase_records_kind",
        ),
        sa.CheckConstraint(
            "(kind IN ('correction', 'void')) = (corrects_record_id IS NOT NULL)",
            name="ck_purchase_records_correction_target",
        ),
        sa.UniqueConstraint("client_request_id", name="uq_purchase_records_client_request"),
    )
    op.create_index("ix_purchase_records_race", "purchase_records", ["race_id"])
    op.execute(_REJECT_FN)
    op.execute(_CREATE_ROW_TRIGGER)
    op.execute(_CREATE_TRUNCATE_TRIGGER)
    # Belt & braces for non-owner roles; the TRUNCATE trigger covers the owner (D9).
    op.execute("REVOKE TRUNCATE ON purchase_records FROM PUBLIC;")


def downgrade() -> None:
    op.execute(_DROP_TRIGGERS)
    op.execute(_DROP_FN)
    op.drop_index("ix_purchase_records_race", table_name="purchase_records")
    op.drop_table("purchase_records")
