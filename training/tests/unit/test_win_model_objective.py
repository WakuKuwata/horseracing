"""Feature 039 US1: WinModel objective switch (cond_logit fit/predict + binary compat)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from horseracing_training.win_model import WinModel


def _synth(n_races=40, field=8, seed=0):
    """Synthetic: score depends on x1; winner = argmax(x1 + noise) per race."""
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(n_races):
        x1 = rng.normal(size=field)
        x2 = rng.normal(size=field)
        util = x1 + rng.normal(scale=0.3, size=field)
        win_idx = int(util.argmax())
        for i in range(field):
            rows.append({"race_id": f"R{r:03d}", "x1": x1[i], "x2": x2[i],
                         "win": 1 if i == win_idx else 0})
    df = pd.DataFrame(rows)
    return df[["x1", "x2"]], df["win"].to_numpy(), df["race_id"].to_numpy()


def test_cond_logit_predict_sums_to_one_and_favors_signal():
    X, y, gids = _synth()
    m = WinModel(seed=42, objective="cond_logit").fit(X, y, group_ids=gids)
    p = m.predict(X, group_ids=gids)
    # per-race Σ=1
    for r in np.unique(gids):
        assert abs(p[gids == r].sum() - 1.0) < 1e-9
    # learned signal: higher x1 -> higher prob (positive rank correlation)
    assert np.corrcoef(X["x1"].to_numpy(), p)[0, 1] > 0.3


def test_cond_logit_predict_requires_group_ids():
    X, y, gids = _synth(n_races=10)
    m = WinModel(seed=1, objective="cond_logit").fit(X, y, group_ids=gids)
    with pytest.raises(ValueError):
        m.predict(X)  # group_ids mandatory for cond_logit


def test_cond_logit_fit_requires_group_ids():
    X, y, _ = _synth(n_races=10)
    with pytest.raises(ValueError):
        WinModel(objective="cond_logit").fit(X, y)


def test_binary_default_unchanged():
    # objective defaults to binary; predict returns per-row P(win) in [0,1]
    X, y, _ = _synth(n_races=20)
    m = WinModel(seed=42).fit(X, y)
    p = m.predict(X)
    assert m.objective == "binary"
    assert p.shape == (len(X),)
    assert np.all((p >= 0) & (p <= 1))


def test_degenerate_single_class_fallback():
    X = pd.DataFrame({"x1": [0.1, 0.2, 0.3], "x2": [1.0, 1.0, 1.0]})
    y = np.array([0, 0, 0])
    gids = np.array(["R", "R", "R"])
    m = WinModel(objective="cond_logit").fit(X, y, group_ids=gids)
    p = m.predict(X, group_ids=gids)  # constant fallback, no exception
    assert p.shape == (3,)
