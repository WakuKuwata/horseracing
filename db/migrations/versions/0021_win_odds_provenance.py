"""win_odds_provenance — when the LATEST stored 単勝 odds of a race were quoted / observed (140)

Revision ID: 0021_win_odds_provenance
Revises: 0020_official_win_payouts
Create Date: 2026-10-11

``race_horses.odds`` is a single latest value (constitution V) and the market-EV rows (0018) freeze
the price they used together with ``odds_observed_at``. Until now that timestamp was a PROXY —
``race_horses.updated_at`` — which moves whenever the row is rewritten for any other reason (an
entries re-ingest, a weight fill, a popularity-only change), so "how long before the post the
price was seen" could not be read back honestly, and a judged-EV model that needs the horizon
``post_time - observed_at`` had nothing to stand on.

Two nullable columns on ``races`` (the quote is one payload = one race = every horse at the same
instant — the exact shape of the 複勝 precedent ``place_odds_official_at`` /
``place_odds_observed_at`` of 0014):

* ``win_odds_official_at`` — the time the SOURCE declares the quote effective
  (``data.official_datetime``). NULL for a FINAL odds value taken from the result page (its
  "official" time is after the close and is not a quote time — plan D3).
* ``win_odds_observed_at`` — when WE fetched the page/JSON the stored value came from (for an
  archive repair: the archive file's timestamp, as for ``official_win_payouts.observed_at``).

Provenance of the single latest value, NOT an odds history: both are overwritten in place with the
value itself. Additive and nullable only — no backfill (plan D2: a proxy must not be dressed up as
provenance; readers fall back to ``updated_at`` and count how often they had to).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_win_odds_provenance"
down_revision = "0020_official_win_payouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "races",
        sa.Column("win_odds_official_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "races",
        sa.Column("win_odds_observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("races", "win_odds_observed_at")
    op.drop_column("races", "win_odds_official_at")
