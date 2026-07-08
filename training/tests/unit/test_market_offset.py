"""Feature 060: market offset — q definition, objective/predict symmetry, fail-closed.

Covers contracts/market-offset.md:
- INV-M1 definition identity (hand-computed 010 vote-share values)
- INV-M2 equivalence: zero-information features + offset -> model reproduces q
- INV-M4 fail-closed on invalid odds (race-level all-or-nothing)
- INV-M5 behavioral leak guard (other-race/result invariance, own-odds positive control)
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.predictor import HorseEntry, RaceContext

from horseracing_training.cond_logit import (
    cond_logit_objective,
    pl_topk_objective,
)
from horseracing_training.dataset import (
    MKT_ODDS,
    RACE_DATE,
    RANK_LABEL,
    WIN_LABEL,
    TrainingMatrix,
)
from horseracing_training.market_offset import (
    log_q_offset,
    offsets_by_race,
    q_from_odds,
    valid_odds_mask,
)
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.win_model import DEFAULT_PARAMS, WinModel

#: params that make every tree contribute exactly 0 (min_child_samples >> n rows means no
#: split is admissible), isolating the offset path: raw score == 0, p == softmax(log q) == q.
#: feature_pre_filter=False keeps LightGBM from dropping the (unsplittable) feature entirely,
#: which would fail its num_features>0 check before training.
_NO_SPLIT_PARAMS = dict(
    DEFAULT_PARAMS, min_child_samples=10**6, feature_pre_filter=False
)

#: params for small synthetic frames (default min_child_samples=20 pre-filters the only
#: feature away on tiny datasets -> LightGBM num_features>0 check fails).
_SMALL_PARAMS = dict(DEFAULT_PARAMS, min_child_samples=2, n_estimators=30)


class _DS:
    def get_label(self):
        return None

    def get_weight(self):
        return None


# --- INV-M1: definition identity (010 vote share) ----------------------------------


def test_q_from_odds_matches_010_hand_values():
    # odds 2.0/4.0/4.0 -> inverse 0.5/0.25/0.25 -> q = 0.5/0.25/0.25 (sums to 1)
    q = q_from_odds([2.0, 4.0, 4.0])
    assert np.allclose(q, [0.5, 0.25, 0.25])
    assert q.sum() == pytest.approx(1.0)
    # overround market: odds 1.5/3.0/6.0 -> inv (2/3, 1/3, 1/6) sum=7/6
    q2 = q_from_odds([1.5, 3.0, 6.0])
    assert np.allclose(q2, [(2 / 3) / (7 / 6), (1 / 3) / (7 / 6), (1 / 6) / (7 / 6)])


def test_log_q_offset_is_log_of_q():
    odds = [2.0, 4.0, 4.0]
    assert np.allclose(log_q_offset(odds), np.log(q_from_odds(odds)))


def test_valid_odds_mask_flags_bad_values():
    mask = valid_odds_mask([2.0, None, np.nan, 0.0, -1.0, np.inf, 1.0])
    assert mask.tolist() == [True, False, False, False, False, False, True]


def test_q_from_odds_fails_closed_on_invalid():
    with pytest.raises(ValueError):
        q_from_odds([2.0, np.nan])
    with pytest.raises(ValueError):
        q_from_odds([])


def test_offsets_by_race_race_level_all_or_nothing():
    race_ids = np.array(["A", "A", "B", "B", "B"])
    odds = np.array([2.0, 4.0, 3.0, np.nan, 5.0])
    offs, eligible = offsets_by_race(race_ids, odds)
    # race A fully covered -> offsets = log q over A only
    assert eligible.tolist() == [True, True, False, False, False]
    assert np.allclose(offs[:2], log_q_offset([2.0, 4.0]))
    # race B has one bad row -> whole race ineligible, offsets NaN
    assert np.isnan(offs[2:]).all()


# --- objective: offset shifts the softmax input -------------------------------------


def test_pl_topk_offsets_equal_shifted_preds():
    preds = np.array([0.4, 0.2, 0.1, -0.3])
    offs = np.array([-1.0, -2.0, -0.5, -3.0])
    ranks = np.array([1, 2, 3, 0])
    g_off, h_off = pl_topk_objective([4], ranks, offsets=offs)(preds, _DS())
    g_ref, h_ref = pl_topk_objective([4], ranks)(preds + offs, _DS())
    assert np.allclose(g_off, g_ref)
    assert np.allclose(h_off, h_ref)


def test_pl_topk_offsets_race_constant_shift_invariant():
    preds = np.array([0.4, 0.2, 0.1, -0.3])
    offs = np.array([-1.0, -2.0, -0.5, -3.0])
    ranks = np.array([1, 2, 3, 0])
    g1, h1 = pl_topk_objective([4], ranks, offsets=offs)(preds, _DS())
    g2, h2 = pl_topk_objective([4], ranks, offsets=offs + 7.0)(preds, _DS())
    assert np.allclose(g1, g2)
    assert np.allclose(h1, h2)


def test_cond_logit_offsets_equal_shifted_preds():
    preds = np.array([0.4, 0.2, 0.1])
    offs = np.array([-1.0, -2.0, -0.5])

    class _DSY:
        def get_label(self):
            return np.array([1.0, 0.0, 0.0])

        def get_weight(self):
            return None

    g_off, _ = cond_logit_objective([3], offsets=offs)(preds, _DSY())
    g_ref, _ = cond_logit_objective([3])(preds + offs, _DSY())
    assert np.allclose(g_off, g_ref)


def test_objective_rejects_non_finite_offsets():
    with pytest.raises(ValueError):
        pl_topk_objective([2], np.array([1, 0]), offsets=np.array([0.0, np.nan]))
    with pytest.raises(ValueError):
        cond_logit_objective([2], offsets=np.array([np.inf, 0.0]))


# --- WinModel: offset train/predict symmetry (fail-closed both ways) -----------------


def _synthetic_frame(n_races: int = 30, n_horses: int = 6, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(n_races):
        rid = f"20240101{r:02d}01"
        date = datetime.date(2024, 1, 1) + datetime.timedelta(days=r)
        odds = rng.uniform(1.5, 50.0, size=n_horses)
        finish = rng.permutation(n_horses) + 1
        for h in range(n_horses):
            rows.append(
                {
                    "race_id": rid,
                    "horse_id": f"h{r}_{h}",
                    "f1": float(rng.normal()),  # noise feature (LightGBM rejects constants)
                    RACE_DATE: date,
                    WIN_LABEL: int(finish[h] == 1),
                    RANK_LABEL: int(finish[h]) if finish[h] <= 3 else 0,
                    MKT_ODDS: float(odds[h]),
                }
            )
    return pd.DataFrame(rows)


def test_win_model_offset_train_predict_mismatch_fails_closed():
    df = _synthetic_frame(n_races=4)
    X = df[["f1"]]
    gids = df["race_id"].to_numpy()
    ranks = df[RANK_LABEL].to_numpy()
    offs, eligible = offsets_by_race(gids, df[MKT_ODDS].to_numpy())
    assert eligible.all()

    m = WinModel(objective="pl_topk", params=dict(_SMALL_PARAMS)).fit(
        X, df[WIN_LABEL].to_numpy(), group_ids=gids, ranks=ranks, offsets=offs
    )
    with pytest.raises(ValueError, match="requires offsets"):
        m.predict(X, group_ids=gids)

    m2 = WinModel(objective="pl_topk", params=dict(_SMALL_PARAMS)).fit(
        X, df[WIN_LABEL].to_numpy(), group_ids=gids, ranks=ranks
    )
    with pytest.raises(ValueError, match="not trained with offsets"):
        m2.predict(X, group_ids=gids, offsets=offs)


def test_win_model_rejects_offsets_for_binary():
    df = _synthetic_frame(n_races=3)
    offs, _ = offsets_by_race(df["race_id"].to_numpy(), df[MKT_ODDS].to_numpy())
    with pytest.raises(ValueError, match="softmax objective"):
        WinModel(objective="binary").fit(
            df[["f1"]], df[WIN_LABEL].to_numpy(), offsets=offs
        )


# --- INV-M2: zero-information features + offset == q --------------------------------


def test_zero_information_offset_model_reproduces_q():
    """No-split params -> trees contribute 0 -> softmax(log q) == q (detects a dropped
    offset on either the fit or the predict side)."""
    df = _synthetic_frame(n_races=30)
    X = df[["f1"]]
    gids = df["race_id"].to_numpy()
    offs, eligible = offsets_by_race(gids, df[MKT_ODDS].to_numpy())
    assert eligible.all()

    m = WinModel(objective="pl_topk", params=dict(_NO_SPLIT_PARAMS)).fit(
        X, df[WIN_LABEL].to_numpy(), group_ids=gids,
        ranks=df[RANK_LABEL].to_numpy(), offsets=offs,
    )
    p = m.predict(X, group_ids=gids, offsets=offs)
    for rid in np.unique(gids):
        sel = gids == rid
        q = q_from_odds(df.loc[sel, MKT_ODDS].to_numpy())
        assert np.allclose(p[sel], q, atol=1e-9), rid


# --- LightGBMPredictor wiring: fit exclusion, predict fail-closed, leak guard --------


def _contexts(df: pd.DataFrame) -> list[RaceContext]:
    out = []
    for rid, g in df.groupby("race_id", sort=True):
        out.append(
            RaceContext(
                race_id=str(rid),
                race_date=g[RACE_DATE].iloc[0],
                started_horses=tuple(HorseEntry(horse_id=h) for h in g["horse_id"]),
            )
        )
    return out


def _predictor_with_frame(df: pd.DataFrame, **kw) -> LightGBMPredictor:
    # calibration="none": a small-sample isotonic step function would flatten q changes and
    # mask the positive control; the calibrated path is covered by the production config runs.
    pred = LightGBMPredictor(
        session=None, objective="pl_topk", calibration="none",
        market_offset=True, params=dict(_SMALL_PARAMS), **kw,
    )
    pred._data = TrainingMatrix(frame=df, feature_cols=["f1"], categorical_cols=[])
    return pred


def test_predictor_market_offset_excludes_partial_races_and_records_counts():
    df = _synthetic_frame(n_races=10)
    # poison one race: one horse without odds -> whole race excluded from training
    bad_rid = df["race_id"].iloc[0]
    df.loc[df.index[0], MKT_ODDS] = np.nan
    pred = _predictor_with_frame(df)
    pred.fit(_contexts(df))
    assert pred.fit_info_["market_offset_excluded_races"] == 1
    assert pred.fit_info_["market_offset_excluded_rows"] == (df["race_id"] == bad_rid).sum()
    assert pred.fit_info_["market_offset"]["kind"] == "log_q_devig"
    assert pred.is_leaky_reference is True


def test_predictor_predict_race_fails_closed_without_full_odds():
    df = _synthetic_frame(n_races=8)
    contexts = _contexts(df)
    pred = _predictor_with_frame(df)
    pred.fit(contexts[:-1])
    # poison the target race's odds AFTER fit -> predict must fail closed, not fall back
    target = contexts[-1]
    mask = df["race_id"] == target.race_id
    df.loc[df[mask].index[0], MKT_ODDS] = np.nan
    with pytest.raises(ValueError, match="fail-closed"):
        pred.predict_race(target)


def test_leak_guard_other_race_odds_and_results_do_not_move_prediction():
    """INV-M5: (i) other races' odds changes leave the target prediction unchanged;
    (ii) result (label) changes after fit leave it unchanged; (iii) the target race's
    own odds change DOES move it (positive control)."""
    df = _synthetic_frame(n_races=12)
    contexts = _contexts(df)
    target = contexts[-1]
    pred = _predictor_with_frame(df)
    pred.fit(contexts[:-1])
    base = pred.predict_race(target)

    # (i) mutate a NON-target race's odds (predict-time read is target-race only)
    other_mask = df["race_id"] == contexts[0].race_id
    df.loc[other_mask, MKT_ODDS] = df.loc[other_mask, MKT_ODDS] * 3.0
    after_other = pred.predict_race(target)
    assert all(base[h].win == after_other[h].win for h in base)

    # (ii) mutate result labels after fit
    df.loc[:, WIN_LABEL] = 0
    df.loc[:, RANK_LABEL] = 0
    after_results = pred.predict_race(target)
    assert all(base[h].win == after_results[h].win for h in base)

    # (iii) positive control: mutate the TARGET race's own odds -> prediction changes
    tgt_mask = df["race_id"] == target.race_id
    odds = df.loc[tgt_mask, MKT_ODDS].to_numpy().copy()
    odds[0] = odds[0] * 10.0
    df.loc[tgt_mask, MKT_ODDS] = odds
    after_own = pred.predict_race(target)
    assert any(base[h].win != after_own[h].win for h in base)


def test_market_offset_requires_softmax_objective():
    with pytest.raises(ValueError, match="softmax objective"):
        LightGBMPredictor(session=None, objective="binary", market_offset=True)


# --- INV-M3: artifacts — market_offset key only for offset models ---------------------


def test_build_preprocessor_market_offset_key_absent_for_ordinary_models():
    from horseracing_training.artifacts import build_preprocessor

    pred = LightGBMPredictor(session=None, objective="pl_topk")
    pred.fit_info_ = {"feature_cols": ["f1"], "categorical_cols": [],
                      "objective": "pl_topk", "postprocess": "group_softmax"}
    prep = build_preprocessor(pred, "features-015")
    assert "market_offset" not in prep  # ordinary models stay byte-identical (INV-M3)


def test_build_preprocessor_market_offset_key_present_for_offset_models():
    from horseracing_training.artifacts import build_preprocessor
    from horseracing_training.market_offset import METADATA

    pred = LightGBMPredictor(session=None, objective="pl_topk", market_offset=True)
    pred.fit_info_ = {"feature_cols": ["f1"], "categorical_cols": [],
                      "objective": "pl_topk", "postprocess": "group_softmax",
                      "market_offset": dict(METADATA)}
    prep = build_preprocessor(pred, "features-015")
    assert prep["market_offset"]["kind"] == "log_q_devig"
