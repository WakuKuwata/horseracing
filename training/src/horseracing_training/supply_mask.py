"""Feature 097: pseudo-supply-death mask for the simulated-regime adoption gate.

The per-horse first-3F feed died (2026: 0.0%); only the 1200m identity derivation survives in
production. To measure a replacement axis under the regime where it matters, the gate re-creates
that steady state INSIDE one uncommitted DB session: every ``race_results.first_3f`` on or after a
pre-registered cutoff is nulled EXCEPT at 1200m (where production keeps deriving it). Both arms are
then rebuilt and retrained from that session and the transaction is rolled back.

Three things here are contracts, not conveniences (contracts/adoption-gate.md):

- **Symmetry** — the mask touches one column and nothing that decides which races are scored or
  which horses started or who won. ``symmetry_snapshot`` taken before and after must be equal.
- **Provenance** — a materialised parquet, a cached frame or a second connection would hand the
  build the UNMASKED column while every set above still matched. ``provenance_violations`` and
  ``projection_rows`` are read through the SAME session right before the matrix is built.
- **No commit** — callers own the transaction; this module never commits.

Lives in training (not ``scripts/``) so the driver and the unit tests import one implementation.
"""

from __future__ import annotations

import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

#: The one distance where first_3f is a derivation, not a feed, in production (ecf0c69).
DERIVABLE_DISTANCE = 1200

PROVENANCE_COLS: tuple[str, ...] = ("race_id", "horse_id", "race_date", "distance", "first_3f")

_MASK = text("""
    UPDATE race_results rr
    SET first_3f = NULL
    FROM races r
    WHERE r.race_id = rr.race_id
      AND r.race_date >= :cutoff
      AND r.distance IS DISTINCT FROM :keep
      AND rr.first_3f IS NOT NULL
""")

_VIOLATIONS = text("""
    SELECT count(*) FROM race_results rr JOIN races r ON r.race_id = rr.race_id
    WHERE r.race_date >= :cutoff AND r.distance IS DISTINCT FROM :keep AND rr.first_3f IS NOT NULL
""")

_PROJECTION = text("""
    SELECT rr.race_id, rr.horse_id, r.race_date, r.distance, rr.first_3f
    FROM race_results rr JOIN races r ON r.race_id = rr.race_id
""")

_SYMMETRY = text("""
    SELECT rh.race_id, rh.horse_id,
           (rr.finish_order = 1 AND rr.result_status = 'finished') AS is_winner
    FROM race_horses rh
    LEFT JOIN race_results rr ON rr.race_id = rh.race_id AND rr.horse_id = rh.horse_id
    WHERE rh.entry_status = 'started'
""")


def apply_first3f_mask(session: Session, cutoff: datetime.date) -> int:
    """Null first_3f on/after ``cutoff`` except at 1200m. Returns rows changed. Never commits."""
    return session.execute(_MASK, {"cutoff": cutoff, "keep": DERIVABLE_DISTANCE}).rowcount


def provenance_violations(session: Session, cutoff: datetime.date) -> int:
    """Rows that should be masked but still carry a value — must be 0 right before the build."""
    params = {"cutoff": cutoff, "keep": DERIVABLE_DISTANCE}
    return int(session.execute(_VIOLATIONS, params).scalar())


def projection_rows(session: Session) -> list[tuple]:
    """The exact projection the provenance hash is taken over (``PROVENANCE_COLS`` order)."""
    return [tuple(r) for r in session.execute(_PROJECTION)]


def symmetry_snapshot(session: Session) -> tuple[frozenset, frozenset, frozenset]:
    """(race_id set, started (race_id, horse_id) set, winner (race_id, horse_id) set)."""
    rows = list(session.execute(_SYMMETRY))
    races = frozenset(r[0] for r in rows)
    started = frozenset((r[0], r[1]) for r in rows)
    winners = frozenset((r[0], r[1]) for r in rows if r[2])
    return races, started, winners
