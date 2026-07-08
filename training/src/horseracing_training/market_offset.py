"""Feature 060: market offset — devig log q from the target race's own win odds.

The market-residual accuracy model adds ``log q`` (q = market vote share, the 010
definition ``q_i = (1/odds_i) / Σ_j (1/odds_j)``) as a per-row offset to the race-softmax
score, so the trees learn only the residual from the market. The offset is NOT a feature
column: it never enters ``feature_cols`` / ``feature_hash`` / FEATURE_VERSION /
feature_snapshots (INV-M1..M3, contracts/market-offset.md).

Definition parity: ``q_from_odds`` is intentionally the same formula as
``horseracing_probability.market_odds.market_implied_win_probs`` (010). training does not
depend on the probability package (probability -> eval dependency direction), so the
formula is duplicated here and frozen by hand-computed unit tests (research D11).

Fail-closed (INV-M4): a race is offset-eligible only if EVERY row has a valid win odds
(finite, > 0). Partially covered races are excluded from training/eval and skipped at
serving — a missing horse's q is never fabricated, and a partial devig would distort the
remaining q's.
"""

from __future__ import annotations

import numpy as np

#: lower clip for q before log (log divergence guard only; q is already in (0, 1]).
Q_CLIP = 1e-6

#: audit marker appended to logic_version when the market offset is applied (INV-M6).
LOGIC_VERSION_FRAGMENT = "mkt=logq"

#: model metadata payload (data-model.md) — recorded at train time, read by serving.
METADATA = {
    "kind": "log_q_devig",
    "source": "race_horses.odds",
    "q_clip": Q_CLIP,
    "limitation": "closing-leaning odds; retrospective accuracy model",
}


def valid_odds_mask(odds) -> np.ndarray:
    """Per-row validity: finite and > 0 (null/NaN/<=0/inf are invalid)."""
    arr = np.asarray(odds, dtype=float)
    return np.isfinite(arr) & (arr > 0.0)


def q_from_odds(odds) -> np.ndarray:
    """Market vote share ``q_i = (1/odds_i) / Σ_j (1/odds_j)`` (010 definition).

    All rows must be valid (caller enforces INV-M4); raises on invalid input rather
    than silently renormalizing over a subset.
    """
    arr = np.asarray(odds, dtype=float)
    if len(arr) == 0:
        raise ValueError("q_from_odds: empty odds")
    if not valid_odds_mask(arr).all():
        raise ValueError("q_from_odds: invalid odds present (fail-closed, INV-M4)")
    inv = 1.0 / arr
    return inv / inv.sum()


def log_q_offset(odds) -> np.ndarray:
    """Per-row offset ``log(clip(q, Q_CLIP, 1))`` for the race-softmax score."""
    q = q_from_odds(odds)
    return np.log(np.clip(q, Q_CLIP, 1.0))


def offsets_by_race(race_ids, odds) -> tuple[np.ndarray, np.ndarray]:
    """Row-aligned offsets for a multi-race frame; races failing INV-M4 are flagged.

    Returns ``(offsets, eligible_row_mask)``: rows of races where every row has valid
    odds get their devig log-q offset; rows of ineligible races get NaN offset and
    ``eligible_row_mask == False``. Row order is preserved (no sorting).
    """
    rid = np.asarray(race_ids)
    arr = np.asarray(odds, dtype=float)
    if len(rid) != len(arr):
        raise ValueError("offsets_by_race: race_ids and odds length mismatch")
    offsets = np.full(len(arr), np.nan, dtype=float)
    eligible = np.zeros(len(arr), dtype=bool)
    valid = valid_odds_mask(arr)
    # group rows by race id (order-preserving; frames are not guaranteed race-contiguous)
    order = np.argsort(rid, kind="stable")
    start = 0
    rid_sorted = rid[order]
    while start < len(rid_sorted):
        end = start
        while end < len(rid_sorted) and rid_sorted[end] == rid_sorted[start]:
            end += 1
        idx = order[start:end]
        if valid[idx].all():
            offsets[idx] = log_q_offset(arr[idx])
            eligible[idx] = True
        start = end
    return offsets, eligible
