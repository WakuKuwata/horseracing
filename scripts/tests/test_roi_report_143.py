"""Financial and paired-day contracts for the new feature comparison."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_143/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi143_report", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def frame():
    return pd.DataFrame({"race_id": ["202001010101"]*3+["202001010102"]*3,
        "horse_id": ["h1", "h2", "h3"]*2, "horse_number": [1, 2, 3]*2,
        "race_date": ["2020-01-01"]*3+["2020-01-02"]*3, "year": [2020]*6,
        "odds": [3., 4., 5.]*2, "won": [1, 0, 0, 0, 1, 0], "field_size": [3]*6,
        "p": [.5, .3, .2, .3, .2, .1]})


def test_manual_profit_has_unspent_day_budget():
    summary, races, bets = M.score(frame())
    # Day1: only h1 EV>1.2; day2: no bets. Total budget2000, deployed1000.
    assert len(bets) == 1
    assert bets.units.tolist() == [10]
    assert races.stake_yen.tolist() == [1000, 0]
    p = summary["policy"]
    assert p["profit_yen"] == 2000
    assert p["budget_yen"] == 2000
    assert p["unspent_cash_yen"] == 1000
    assert p["deployed_roi"] == 3
    assert p["budget_return"] == 1
    assert p["profit_without_top5_payouts_yen"] == -1000


def test_same_model_differences_zero_even_with_no_bet_day():
    _, races, _ = M.score(frame())
    result = M.paired({"baseline": races, "speed": races.copy(), "pace": races.copy()}, repetitions=50)
    assert result["family_size_per_metric"] == 3
    for arm in result["results"].values():
        for metric in arm.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_unmatched_skipped_day_rejected():
    _, races, _ = M.score(frame())
    with pytest.raises(ValueError, match="populations differ"):
        M.paired({"baseline": races, "speed": races.iloc[:1]}, repetitions=10)


def test_zero_probabilities_same_finite_nll_in_both_reports():
    d = frame().iloc[:3].copy()
    d["p"] = [0, .5, .5]
    summary, races, _ = M.score(d)
    assert np.isfinite(races.winner_nll).all()
    assert summary["quality"]["winner_nll"] == pytest.approx(races.winner_nll.mean())


def test_paired_reports_roi_and_budget_profit_as_different_estimands():
    _, races, _ = M.score(frame())
    candidate = races.copy()
    candidate["stake_yen"] /= 2
    candidate["payout_yen"] /= 2
    candidate["profit_yen"] /= 2
    result = M.paired({"baseline": races, "speed": candidate}, repetitions=30)["results"]["speed"]
    assert result["deployed_roi"]["difference"] == 0
    assert result["profit_per_budget"]["difference"] == -.5
