"""Status-aware training-label derivation (US2 / INV-3, INV-5).

Only ``result_status = 'finished'`` rows are included; non-starters
(cancelled/excluded — which have no race_results row) and non-finishers
(stopped/disqualified) are excluded. Dead heats are represented by shared
``finish_order`` so ``<=`` based labels naturally allow multiple winners.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

LABEL_QUERY = text(
    """
    SELECT
        race_id,
        horse_id,
        finish_order,
        (finish_order = 1)::int  AS win,
        (finish_order <= 2)::int AS top2,
        (finish_order <= 3)::int AS top3
    FROM race_results
    WHERE result_status = 'finished'
      AND race_id = :race_id
    ORDER BY finish_order, horse_id
    """
)


def derive_labels(session: Session, race_id: str) -> Sequence[dict]:
    """Return per-horse {win, top2, top3} labels for the finishers of one race."""
    rows = session.execute(LABEL_QUERY, {"race_id": race_id}).mappings().all()
    return [dict(r) for r in rows]
