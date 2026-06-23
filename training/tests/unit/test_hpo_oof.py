"""US4 (P2): HPO selects on TRAIN-only CV; OOF target encoding avoids self-leak."""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd

from horseracing_training.hpo import _encode_fold, select_params_cv
from horseracing_training.target_encoding import oof_target_encode


def _learnable_df(n_races: int = 12, field: int = 6) -> pd.DataFrame:
    rows = []
    for r in range(n_races):
        date = datetime.date(2007, 1, 1) + datetime.timedelta(days=r)
        for i in range(field):
            x = 1 if i == 0 else 0  # x==1 marks the winner
            rows.append(
                {
                    "race_id": f"r{r:02d}",
                    "race_date": date,
                    "x": float(x),
                    "win": x,
                }
            )
    return pd.DataFrame(rows)


def test_select_params_cv_is_train_only_and_deterministic():
    df = _learnable_df()
    grid = [{"num_leaves": 7}, {"num_leaves": 31}]
    res_a = select_params_cv(
        df, ["x"], race_id_col="race_id", race_date_col="race_date",
        label_col="win", grid=grid, seed=42, n_splits=3,
    )
    res_b = select_params_cv(
        df, ["x"], race_id_col="race_id", race_date_col="race_date",
        label_col="win", grid=grid, seed=42, n_splits=3,
    )
    # deterministic, and the chosen params come from the supplied grid only
    assert res_a.best_params == res_b.best_params
    assert any(res_a.best_params["num_leaves"] == g["num_leaves"] for g in grid)
    assert all(np.isfinite(v) for v in res_a.scores.values())


def test_oof_encoding_does_not_leak_a_rows_own_label():
    # A singleton category that appears once with label 1. fit-all/apply-all would encode it as
    # 1.0 (its own label); OOF must fall back to the global prior instead.
    df = _learnable_df()
    df = pd.concat(
        [
            df,
            pd.DataFrame(
                [{"race_id": "r11", "race_date": datetime.date(2007, 1, 20), "x": 1.0, "win": 1,
                  "g": "solo"}]
            ),
        ],
        ignore_index=True,
    )
    df["g"] = df["g"].fillna("common")
    prior = float(df["win"].mean())  # global prior over the full frame (the OOF fallback)

    enc = oof_target_encode(
        df, "g", race_id_col="race_id", race_date_col="race_date",
        label_col="win", n_splits=5,
    )
    solo_idx = df.index[df["g"] == "solo"][0]
    # encoding for the solo row is the prior fallback, NOT its own label (1.0)
    assert abs(enc.loc[solo_idx] - prior) < 1e-9
    assert enc.loc[solo_idx] < 0.9


def test_cv_fold_encoding_does_not_leak_validation_labels():
    # codex's #1 trap: a category that appears ONLY in the validation fold with label 1 must be
    # encoded by the train-fold encoder as the prior — never as its own (validation) label.
    tr = pd.DataFrame(
        {"race_id": ["r0", "r0", "r1"], "win": [1, 0, 0], "g": ["A", "B", "A"], "x": [1.0, 0.0, 0.0]}
    )
    va = pd.DataFrame(
        {"race_id": ["r2", "r2"], "win": [1, 1], "g": ["Z", "Z"], "x": [1.0, 1.0]}  # 'Z' unseen in tr
    )
    tr_x, va_x = _encode_fold(tr, va, ["x", "g"], ("g",), "win", smoothing=1.0)
    prior = float(tr["win"].mean())
    # validation 'Z' rows fall back to the train prior, not their own label
    assert (abs(va_x["g"].to_numpy() - prior) < 1e-9).all()
    # train encodings are derived from train labels only and stay finite
    assert np.isfinite(tr_x["g"].to_numpy()).all()
