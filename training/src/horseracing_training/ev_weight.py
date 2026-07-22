"""Feature 079: EV-based per-RACE training weight (retrospective kill-test).

Pure, deterministic weight builder. Given per-row OOF win prob (frozen, market-independent,
strict-past) and market odds, produce a per-row LightGBM sample weight that is CONSTANT within
each race (a per-race scalar alpha_r). Applying a race-constant weight scales that race's whole
PL listwise loss by a constant (L_r' = alpha_r * L_r), which is a valid weighted PL likelihood —
the per-horse form is NOT (codex #1: sum_i w_i(p_i - y_i) != 0 breaks the zero-sum stage gradient).

LOCKED formula (pre-registration 079 sec 2.2; do NOT tune on results):

    EV_i   = oof_p_i * odds_i
    C_r    = { started horses in race r with valid oof_p AND valid odds AND odds < ODDS_CAP }
    ev_r   = max_{i in C_r} EV_i     (0.0 if C_r is empty)
    raw_r  = 1 + sigmoid((ev_r - CENTER) / TAU)
    alpha_r = raw_r / mean_{informative r}(raw_r)   # normalise informative races to mean 1

COMPLETE-FIELD rule (codex #8, mirrors 060 offsets_by_race fail-closed): a race is *informative*
only if EVERY row it contributes has valid oof_p AND valid odds. If ANY row in a race is missing
either, the race is *neutral* (alpha_r = 1.0 exactly) — we never take a partial-field maximum.
Earliest folds without OOF coverage fall here automatically. Neutral races sit at weight 1.0 (the
informative mean), so the overall gradient scale is ~unchanged and missing data is not
differentially weighted.

This module reads odds ONLY to build a training weight; odds are never a feature and the resulting
model is explicitly market-aware (079 is artifact-only, never active/default).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: pre-registered, fixed before any run (079 sec 2.2). Break-even EV = 1.0.
CENTER: float = 1.0
TAU: float = 0.10
ODDS_CAP: float = 21.0


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # stable logistic
    return np.where(z >= 0, 1.0 / (1.0 + np.exp(-z)), np.exp(z) / (1.0 + np.exp(z)))


def build_race_weights(
    race_ids,
    oof_p,
    odds,
    *,
    center: float = CENTER,
    tau: float = TAU,
    odds_cap: float = ODDS_CAP,
) -> np.ndarray:
    """Per-row weight, constant within each race (row-aligned to ``race_ids``).

    ``oof_p`` / ``odds``: per-row float arrays; use NaN for missing (no OOF coverage / no odds).
    Returns float64 weights with mean ~1 over informative races; neutral races = 1.0 exactly.
    Deterministic; independent of row order (race grouping is by value, output is per-row).
    """
    race_ids = np.asarray(race_ids)
    p = np.asarray(oof_p, dtype=float)
    o = np.asarray(odds, dtype=float)
    n = len(race_ids)
    if not (len(p) == len(o) == n):
        raise ValueError("race_ids, oof_p, odds must be the same length")

    valid = np.isfinite(p) & np.isfinite(o)  # row has both OOF p and odds
    ev = np.where(valid, p * o, np.nan)
    cap_ok = valid & (o < odds_cap)  # cap-eligible rows contribute to the max

    # group rows by race id (stable, value-based)
    codes, uniq = pd.factorize(race_ids, sort=False)
    n_races = len(uniq)

    # informative race iff EVERY row in it is valid (complete field) -> no partial-field max
    rows_per_race = np.bincount(codes, minlength=n_races)
    valid_per_race = np.bincount(codes, weights=valid.astype(float), minlength=n_races)
    informative = valid_per_race == rows_per_race  # all rows valid

    # ev_r = max EV over cap-eligible rows; -inf sentinel where no cap-eligible row
    ev_for_max = np.where(cap_ok, ev, -np.inf)
    race_max = np.full(n_races, -np.inf)
    np.maximum.at(race_max, codes, ev_for_max)
    ev_r = np.where(np.isfinite(race_max), race_max, 0.0)  # empty cap-set -> 0.0

    raw_r = 1.0 + _sigmoid((ev_r - center) / tau)

    # normalise informative races to mean 1; neutral races forced to exactly 1.0
    if informative.any():
        mean_raw = float(raw_r[informative].mean())
    else:
        mean_raw = 1.0
    alpha_r = np.where(informative, raw_r / mean_raw, 1.0)

    return alpha_r[codes].astype(float)


def assert_race_constant(race_ids, weights, *, rtol: float = 0.0, atol: float = 1e-12) -> None:
    """Fail-closed: every row of a race must share one weight (test #2 / validity guard).

    Guards the codex #1 invariant at the seam that passes weights into WinModel: a per-horse
    weight is not a valid PL likelihood, so a non-race-constant vector must be rejected, not
    silently fit.
    """
    race_ids = np.asarray(race_ids)
    w = np.asarray(weights, dtype=float)
    if len(race_ids) != len(w):
        raise ValueError("race_ids and weights length mismatch")
    codes, uniq = pd.factorize(race_ids, sort=False)
    n_races = len(uniq)
    wmin = np.full(n_races, np.inf)
    wmax = np.full(n_races, -np.inf)
    np.minimum.at(wmin, codes, w)
    np.maximum.at(wmax, codes, w)
    spread = wmax - wmin
    tol = atol + rtol * np.abs(wmax)
    bad = spread > tol
    if bad.any():
        r = int(np.argmax(bad))
        raise ValueError(
            f"weights not constant within race {uniq[r]!r}: "
            f"spread {spread[r]} > tol {tol[r]} "
            "(per-horse weight is not a valid PL likelihood)"
        )
