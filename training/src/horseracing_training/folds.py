"""Deterministic race-level chronological folds, shared by HPO and OOF target encoding.

Splitting by whole races (never by horse rows) guarantees no race straddles a fold
boundary — the prerequisite for leak-free CV and out-of-fold encoding (codex).
"""

from __future__ import annotations

import numpy as np


def chronological_race_folds(race_ids, race_dates, n_splits: int) -> list[set[str]]:
    """Partition the distinct races into ``n_splits+1`` time-ordered chunks (race_id sets).

    Races are ordered by ``(date, race_id)`` for determinism. Returns ``n_splits+1`` chunks
    so callers can use chunk[i] as a validation fold with chunk[:i] as its expanding train.
    """
    uniq = sorted({rid: race_dates[rid] for rid in race_ids}.items(), key=lambda kv: (kv[1], kv[0]))
    race_order = [rid for rid, _ in uniq]
    if len(race_order) < n_splits + 1:
        n_splits = max(1, len(race_order) - 1)
    chunks = np.array_split(race_order, n_splits + 1)
    return [set(c.tolist()) for c in chunks]
