"""Feature 079 (step 1): sample-weight threading through WinModel.

Scope here = the PLUMBING only (a generic per-row weight reaches lgb.Dataset(weight=) and
the objective's get_weight() multiply). The EV-weight *scheme* and its race-constancy /
leak invariants are built + tested with the weight builder (step 2), not here.

Locked invariants (pre-registration test #1, and the row-alignment bug class from 062):
- uniform weight (alpha_r == 1) is BYTE-IDENTICAL to the no-weight path;
- a non-uniform race-constant weight actually changes the fit yet keeps per-race Sigma=1;
- weights track rows through the objective's internal stable sort by race id.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from horseracing_training.win_model import WinModel


def _synth(n_races=40, field=8, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(n_races):
        x1 = rng.normal(size=field)
        x2 = rng.normal(size=field)
        util = x1 + rng.normal(scale=0.3, size=field)
        order = np.argsort(-util)
        rank = np.zeros(field, dtype=int)
        for pos in range(min(3, field)):
            rank[order[pos]] = pos + 1
        for i in range(field):
            rows.append({"race_id": f"R{r:03d}", "x1": x1[i], "x2": x2[i],
                         "win": 1 if rank[i] == 1 else 0, "rank": rank[i]})
    df = pd.DataFrame(rows)
    return df


def _fit_predict(df, *, objective, weights=None):
    X = df[["x1", "x2"]]
    y = df["win"].to_numpy()
    gids = df["race_id"].to_numpy()
    ranks = df["rank"].to_numpy()
    kw = {"group_ids": gids}
    if objective == "pl_topk":
        kw["ranks"] = ranks
    m = WinModel(seed=42, objective=objective).fit(X, y, weights=weights, **kw)
    return m.predict(X, group_ids=gids)


@pytest.mark.parametrize("objective", ["cond_logit", "pl_topk"])
def test_uniform_weight_is_byte_identical_to_no_weight(objective):
    """alpha_r == 1 must reproduce the pre-079 prediction byte-for-byte (test #1)."""
    df = _synth()
    p_none = _fit_predict(df, objective=objective, weights=None)
    p_ones = _fit_predict(df, objective=objective, weights=np.ones(len(df)))
    assert np.array_equal(p_none, p_ones)


@pytest.mark.parametrize("objective", ["cond_logit", "pl_topk"])
def test_race_constant_weight_takes_effect_and_keeps_sum_one(objective):
    """A non-uniform per-race scalar changes the fit but preserves per-race Sigma=1."""
    df = _synth()
    gids = df["race_id"].to_numpy()
    # per-race scalar in {0.5, 1.5} by race index parity -> broadcast to that race's rows
    uniq = pd.unique(gids)
    alpha_by_race = {r: (1.5 if i % 2 == 0 else 0.5) for i, r in enumerate(uniq)}
    w = np.array([alpha_by_race[r] for r in gids], dtype=float)
    p_w = _fit_predict(df, objective=objective, weights=w)
    p_0 = _fit_predict(df, objective=objective, weights=None)
    # weight is live: predictions differ from the unweighted fit
    assert not np.allclose(p_w, p_0)
    # still a valid per-race distribution
    for r in np.unique(gids):
        assert abs(p_w[gids == r].sum() - 1.0) < 1e-9


@pytest.mark.parametrize("objective", ["cond_logit", "pl_topk"])
def test_weights_track_rows_through_internal_sort(objective):
    """Row-alignment: shuffling rows + their weights identically must yield the same
    per-row predictions (the fit sorts by race id internally; weights must follow)."""
    df = _synth(n_races=25, seed=3)
    gids = df["race_id"].to_numpy()
    uniq = pd.unique(gids)
    alpha_by_race = {r: 0.7 + 0.6 * (i % 3) for i, r in enumerate(uniq)}  # 0.7/1.3/1.9
    w = np.array([alpha_by_race[r] for r in gids], dtype=float)

    p_ref = _fit_predict(df, objective=objective, weights=w)

    rng = np.random.default_rng(11)
    perm = rng.permutation(len(df))
    df_sh = df.iloc[perm].reset_index(drop=True)
    w_sh = w[perm]
    p_sh = _fit_predict(df_sh, objective=objective, weights=w_sh)

    # map shuffled predictions back to original row order and compare
    inv = np.empty_like(perm)
    inv[perm] = np.arange(len(perm))
    assert np.allclose(p_sh[inv], p_ref, atol=1e-9)


def test_binary_weight_path_smoke():
    """Binary path threads sample_weight without error and stays in [0,1]."""
    df = _synth(n_races=20)
    X, y = df[["x1", "x2"]], df["win"].to_numpy()
    w = np.linspace(0.5, 1.5, len(df))
    m = WinModel(seed=42).fit(X, y, weights=w)
    p = m.predict(X)
    assert p.shape == (len(df),)
    assert np.all((p >= 0) & (p <= 1))
