"""Outcome-free common policy, official-payout accounting, and paired-day uncertainty."""
from __future__ import annotations

import numpy as np
import pandas as pd

ARMS = {
    "roi_only": ["q", "p_mev"],
    "plus_prediction": ["q", "p_mev", "p_pred_preweight"],
}


def probabilities(frame, coefficients):
    result = {}
    for name, columns in ARMS.items():
        values = frame[columns].to_numpy(float)
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError("Invalid component probability")
        beta = np.array([coefficients[name]["coefficients"][c] for c in columns])
        if coefficients[name]["fit_through"] >= frame.race_date.min():
            raise ValueError("Coefficient fit crosses evaluation")
        score = pd.Series(np.log(values) @ beta, index=frame.index)
        shifted = score - score.groupby(frame.race_id).transform("max")
        exp = np.exp(shifted)
        p = exp / exp.groupby(frame.race_id).transform("sum")
        if not np.allclose(p.groupby(frame.race_id).sum(), 1., atol=1e-12, rtol=0):
            raise ValueError("Blend is not normalized")
        result[name] = p.to_numpy()
    return result


def select_tickets(frame, probability):
    p = np.asarray(probability, dtype=float)
    if len(p) != len(frame) or not np.isfinite(p).all() or (p <= 0).any() or (p >= 1).any():
        raise ValueError("Invalid policy probability")
    if not np.allclose(pd.Series(p).groupby(frame.race_id.to_numpy()).sum(), 1., atol=1e-10, rtol=0):
        raise ValueError("Incomplete decision field")
    if frame.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Duplicate policy identity")
    d = frame[["race_id", "horse_id", "horse_number", "odds"]].reset_index(drop=True).copy()
    d["ev"] = p * d.odds.to_numpy(float)
    if not np.isfinite(d.odds).all() or (d.odds < 1).any():
        raise ValueError("Invalid final price")
    ranked = d.loc[d.ev > 1.].sort_values(
        ["race_id", "ev", "horse_number", "horse_id"],
        ascending=[True, False, True, True], kind="stable")
    selected = ranked.groupby("race_id", sort=False).head(10).index
    mask = np.zeros(len(d), dtype=bool)
    mask[selected] = True
    return mask, d.ev.to_numpy()


def account(frame, selected):
    """All eligible races remain in the denominator, including zero-stake races."""
    selected = np.asarray(selected, dtype=bool)
    dividend = frame.official_payout_yen.to_numpy(float)
    if len(selected) != len(frame) or not np.isfinite(dividend).all() or (dividend < 0).any():
        raise ValueError("Missing official settlement")
    if not np.array_equal(dividend > 0, frame.won.to_numpy(bool)):
        raise ValueError("Official dividend/winner mismatch")
    if ((dividend > 0) & (dividend < 100)).any():
        raise ValueError("Invalid winning dividend")
    d = frame[["race_id", "race_date", "post_time"]].copy()
    d["stake"] = selected.astype(int) * 100
    d["payout"] = selected.astype(int) * dividend
    d["tickets"] = selected.astype(int)
    d["hits"] = (selected & frame.won.to_numpy(bool)).astype(int)
    result = d.groupby(["race_id", "race_date", "post_time"], dropna=False, sort=False)[
        ["stake", "payout", "tickets", "hits"]].sum().reset_index()
    result["profit"] = result.payout - result.stake
    result["budget"] = 1000
    if (result.stake > result.budget).any():
        raise ValueError("Policy exceeded common budget")
    return result.sort_values(["race_date", "post_time", "race_id"]).reset_index(drop=True)


def summarize(races, frame, p):
    stake = int(races.stake.sum())
    payout = int(races.payout.sum())
    profit = payout - stake
    cumulative = np.r_[0., races.profit.to_numpy(float).cumsum()]
    return dict(races=len(races), days=int(races.race_date.nunique()), tickets=int(races.tickets.sum()),
        hit_tickets=int(races.hits.sum()), stake_yen=stake, payout_yen=payout, profit_yen=profit,
        roi=payout / stake if stake else None, no_bet_races=int(races.stake.eq(0).sum()),
        common_budget_yen=1000 * len(races), profit_per_common_budget=profit / (1000 * len(races)),
        winner_nll=float(-np.log(np.asarray(p)[frame.won.to_numpy(bool)]).mean()),
        max_drawdown_yen=float((np.maximum.accumulate(cumulative) - cumulative).max()))


def paired(races_a, races_b, *, repetitions=4000, seed=20261026):
    """B minus A. Two financial intervals receive Bonferroni simultaneous 95% coverage.

    Never remove undefined zero-stake replicates to make a conditional ROI interval.
    """
    keys = ["race_id", "race_date"]
    d = races_a.merge(races_b, on=keys, suffixes=("_a", "_b"), validate="one_to_one")
    if len(d) != len(races_a) or len(d) != len(races_b):
        raise ValueError("Unpaired comparison populations")
    daily = d.groupby("race_date", sort=True)[
        ["stake_a", "payout_a", "profit_a", "budget_a", "stake_b", "payout_b", "profit_b"]].sum()
    data = daily.to_numpy(float)
    rng = np.random.default_rng(seed)
    totals = data[rng.integers(0, len(data), size=(repetitions, len(data)))].sum(axis=1)
    valid = (totals[:, 0] > 0) & (totals[:, 4] > 0)
    roi = np.full(repetitions, np.nan)
    roi[valid] = totals[valid, 5] / totals[valid, 4] - totals[valid, 1] / totals[valid, 0]
    profit_budget = (totals[:, 6] - totals[:, 2]) / totals[:, 3]

    def interval(values):
        return [float(x) for x in np.quantile(values, [.0125, .9875])]

    stake_a, stake_b = races_a.stake.sum(), races_b.stake.sum()
    point_roi = (races_b.payout.sum() / stake_b - races_a.payout.sum() / stake_a
                 if stake_a > 0 and stake_b > 0 else None)
    race_diffs = d["profit_b"] - d["profit_a"]
    largest = int(np.abs(race_diffs).to_numpy().argmax())
    return dict(days=len(daily), races=len(d), repetitions=repetitions, seed=seed,
        interval="97.5% per metric; Bonferroni simultaneous 95% for ROI and profit/common budget",
        roi_difference=point_roi, roi_difference_interval=interval(roi) if valid.all() else None,
        undefined_roi_replicates=int((~valid).sum()),
        profit_difference_yen=int(races_b.profit.sum() - races_a.profit.sum()),
        profit_common_budget_difference=float((races_b.profit.sum() - races_a.profit.sum()) / races_a.budget.sum()),
        profit_common_budget_interval=interval(profit_budget),
        largest_absolute_race_difference=dict(race_id=d.iloc[largest].race_id,
            difference_yen=float(race_diffs.iloc[largest])),
        profit_difference_without_that_race_yen=float(race_diffs.sum() - race_diffs.iloc[largest]))
