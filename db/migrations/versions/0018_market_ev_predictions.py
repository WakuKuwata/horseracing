"""market_ev_predictions — expected return (期待回収率) of a separate market-aware model (137)

Revision ID: 0018_market_ev_predictions
Revises: 0017_purchase_records
Create Date: 2026-09-30

The display shows, per started horse, ``expected_return = win_prob × odds_used`` from a
market-aware model (`mev-binary-v2`) that takes the CURRENT win odds as an input. That model is
deliberately kept OUTSIDE the main win-probability lineage (constitution II: odds never enter the
main model; this feature is the separate spec that tries them):

* no FK to ``model_versions`` — the main pipeline's single-active assumption must not see it;
* no FK to ``horses`` — every row is a regenerable derived value, so the entity re-key (067)
  does not need to touch it;
* the stored values never re-enter any feature, training or calibration input (leak guard:
  ``features/tests/unit/test_market_ev_leak_guard.py``).

Single latest value per (race_id, model_version, horse_id) and NO history (constitution V): a
recompute replaces the race's rows in one transaction. Each row still carries its provenance —
the odds value it used, when those odds were observed, whether the race was result-pending at
compute time, the booster file + sha256, the logic version and the run id.

``win_prob`` is the raw binary-classifier output, NOT normalized within the race (constitution
IV), so it is stored for audit only and is never shown as a 1着率.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_market_ev_predictions"
down_revision = "0017_purchase_records"
branch_labels = None
depends_on = None

# Literal, not imported: a migration is a historical record (0015/0017 precedent). op.f() keeps
# the constraint names exactly as written (0013 precedent) instead of letting the metadata naming
# convention prefix them a second time.
_TABLE = "market_ev_predictions"
_INDEX = "ix_market_ev_predictions_model_version_computed_at"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("race_id", sa.Text(), sa.ForeignKey("races.race_id"), nullable=False),
        sa.Column("horse_id", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("horse_number", sa.Integer(), nullable=True),
        sa.Column("win_prob", sa.Numeric(), nullable=False),
        sa.Column("odds_used", sa.Numeric(), nullable=False),
        # = win_prob × odds_used, stored at write time
        sa.Column("expected_return", sa.Numeric(), nullable=False),
        # race_horses.updated_at of the odds that were used
        sa.Column("odds_observed_at", sa.DateTime(timezone=True), nullable=False),
        # OBSERVED fact: no race_results row existed at compute time
        sa.Column("result_pending_at_compute", sa.Boolean(), nullable=False),
        sa.Column("booster", sa.Text(), nullable=False),
        sa.Column("booster_sha256", sa.Text(), nullable=False),
        sa.Column("logic_version", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("race_id", "model_version", "horse_id",
                                name=op.f("pk_market_ev_predictions")),
        sa.CheckConstraint("win_prob > 0 AND win_prob < 1",
                           name=op.f("ck_market_ev_predictions_win_prob")),
        sa.CheckConstraint("odds_used >= 1.0",
                           name=op.f("ck_market_ev_predictions_odds_used")),
        sa.CheckConstraint("expected_return >= 0",
                           name=op.f("ck_market_ev_predictions_expected_return")),
    )
    op.create_index(_INDEX, _TABLE, ["model_version", "computed_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
