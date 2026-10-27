"""Four planned contrasts preserve the comparator, cash and race calendar."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_144/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi_report144", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CONTRASTS = [[a, b] for a in ("within_race", "race_only") for b in ("baseline", "weighted_baseline")]


def table():
    return pd.DataFrame(dict(race_id=["202001010101", "202001010102"], race_date=["2020-01-01", "2020-01-02"],
        stake_yen=[1000., 0.], payout_yen=[3000., 0.], profit_yen=[2000., 0.], budget_yen=[1000, 1000], winner_nll=[1., 2.]))


def test_four_identical_contrasts_zero_with_no_bet_day():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "within_race", "race_only")}
    result = M.paired(frames, CONTRASTS, repetitions=60)
    assert result["family_size_per_metric"] == 4
    assert len(result["results"]) == 4
    for rec in result["results"].values():
        for metric in rec.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_comparator_changes_profit_but_does_not_force_equal_stakes():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "within_race", "race_only")}
    for c in ("stake_yen", "payout_yen", "profit_yen"):
        frames["weighted_baseline"][c] /= 2
    result = M.paired(frames, CONTRASTS, repetitions=20)["results"]
    assert result["within_race_minus_baseline"]["profit_per_budget"]["difference"] == 0
    assert result["within_race_minus_weighted_baseline"]["profit_per_budget"]["difference"] == .5
    assert result["within_race_minus_weighted_baseline"]["deployed_roi"]["difference"] == 0


def test_unmatched_nonbet_day_and_incorrect_family_rejected():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "within_race", "race_only")}
    frames["within_race"] = frames["within_race"].iloc[:1]
    with pytest.raises(ValueError, match="populations"):
        M.paired(frames, CONTRASTS, repetitions=5)
    frames["within_race"] = table()
    with pytest.raises(ValueError, match="family"):
        M.paired(frames, CONTRASTS[:2], repetitions=5)


def test_same_race_deleted_from_both_when_measuring_influence():
    reference = table()
    reference["race_id"] = ["202608030111", "202601010101"]
    reference["race_date"] = ["2026-04-25", "2026-01-01"]
    new = reference.copy()
    new["profit_yen"] += [100, -40]
    result = M.sensitivity({"baseline": reference, "within_race": new}, [["within_race", "baseline"]])
    rec = result["within_race_minus_baseline"]
    assert rec["known_race_delta_yen"] == 100
    assert rec["without_known_race_delta_yen"] == -40
    assert rec["without_top_difference_yen"] == -40
    assert rec["same_calendar_jan1_jun21_by_year"] == {"2026": 60}
