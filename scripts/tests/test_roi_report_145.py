"""Three planned contrasts preserve the comparator, cash and race calendar."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_145/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi_report145", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CONTRASTS = [["weighted_pace", b] for b in ("baseline", "weighted_baseline", "pace")]


def table():
    return pd.DataFrame(dict(race_id=["202001010101", "202001010102"], race_date=["2020-01-01", "2020-01-02"],
        stake_yen=[1000., 0.], payout_yen=[3000., 0.], profit_yen=[2000., 0.], budget_yen=[1000, 1000], winner_nll=[1., 2.]))


def test_three_identical_contrasts_zero_with_no_bet_day():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "weighted_pace", "pace")}
    result = M.paired(frames, CONTRASTS, repetitions=60)
    assert result["family_size_per_metric"] == 3
    assert len(result["results"]) == 3
    for rec in result["results"].values():
        for metric in rec.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_comparator_changes_profit_but_does_not_force_equal_stakes():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "weighted_pace", "pace")}
    for c in ("stake_yen", "payout_yen", "profit_yen"):
        frames["weighted_baseline"][c] /= 2
    result = M.paired(frames, CONTRASTS, repetitions=20)["results"]
    assert result["weighted_pace_minus_baseline"]["profit_per_budget"]["difference"] == 0
    assert result["weighted_pace_minus_weighted_baseline"]["profit_per_budget"]["difference"] == .5
    assert result["weighted_pace_minus_weighted_baseline"]["deployed_roi"]["difference"] == 0


def test_unmatched_nonbet_day_and_incorrect_family_rejected():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "weighted_pace", "pace")}
    frames["weighted_pace"] = frames["weighted_pace"].iloc[:1]
    with pytest.raises(ValueError, match="populations"):
        M.paired(frames, CONTRASTS, repetitions=5)
    frames["weighted_pace"] = table()
    with pytest.raises(ValueError, match="family"):
        M.paired(frames, CONTRASTS[:2], repetitions=5)


def test_same_race_deleted_from_both_when_measuring_influence():
    reference = table()
    reference["race_id"] = ["202608030111", "202601010101"]
    reference["race_date"] = ["2026-04-25", "2026-01-01"]
    new = reference.copy()
    new["profit_yen"] += [100, -40]
    result = M.sensitivity({"baseline": reference, "weighted_pace": new}, [["weighted_pace", "baseline"]])
    rec = result["weighted_pace_minus_baseline"]
    assert rec["known_race_delta_yen"] == 100
    assert rec["without_known_race_delta_yen"] == -40
    assert rec["without_top_difference_yen"] == -40
    assert rec["same_calendar_jan1_jun21_by_year"] == {"2026": 60}
