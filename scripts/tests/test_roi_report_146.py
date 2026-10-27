"""Two planned contrasts preserve the comparator, cash and race calendar."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_146/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi_report146", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CONTRASTS = [["weighted_backfill", b] for b in ("baseline", "weighted_baseline")]


def table():
    return pd.DataFrame(dict(race_id=["202001010101", "202001010102"], race_date=["2020-01-01", "2020-01-02"],
        stake_yen=[1000., 0.], payout_yen=[3000., 0.], profit_yen=[2000., 0.], budget_yen=[1000, 1000], winner_nll=[1., 2.]))


def test_two_identical_contrasts_zero_with_no_bet_day():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "weighted_backfill")}
    result = M.paired(frames, CONTRASTS, repetitions=60)
    assert result["family_size_per_metric"] == 2
    assert len(result["results"]) == 2
    for rec in result["results"].values():
        for metric in rec.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_comparator_changes_profit_but_does_not_force_equal_stakes():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "weighted_backfill")}
    for c in ("stake_yen", "payout_yen", "profit_yen"):
        frames["weighted_baseline"][c] /= 2
    result = M.paired(frames, CONTRASTS, repetitions=20)["results"]
    assert result["weighted_backfill_minus_baseline"]["profit_per_budget"]["difference"] == 0
    assert result["weighted_backfill_minus_weighted_baseline"]["profit_per_budget"]["difference"] == .5
    assert result["weighted_backfill_minus_weighted_baseline"]["deployed_roi"]["difference"] == 0


def test_unmatched_nonbet_day_and_incorrect_family_rejected():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "weighted_backfill")}
    frames["weighted_backfill"] = frames["weighted_backfill"].iloc[:1]
    with pytest.raises(ValueError, match="populations"):
        M.paired(frames, CONTRASTS, repetitions=5)
    frames["weighted_backfill"] = table()
    with pytest.raises(ValueError, match="family"):
        M.paired(frames, CONTRASTS[:1], repetitions=5)


def test_same_race_deleted_from_both_when_measuring_influence():
    reference = table()
    reference["race_id"] = ["202608030111", "202601010101"]
    reference["race_date"] = ["2026-04-25", "2026-01-01"]
    new = reference.copy()
    new["profit_yen"] += [100, -40]
    result = M.sensitivity({"baseline": reference, "weighted_backfill": new}, [["weighted_backfill", "baseline"]])
    rec = result["weighted_backfill_minus_baseline"]
    assert rec["known_race_delta_yen"] == 100
    assert rec["without_known_race_delta_yen"] == -40
    assert rec["without_top_difference_yen"] == -40
    assert rec["same_calendar_jan1_jun21_by_year"] == {"2026": 60}


def teacher(years):
    return pd.DataFrame([dict(race_id=f"{year}01010101", horse_id=f"{year}-{horse}",
        race_date=f"{year}-01-01", year=year, p_oof=.7 if horse == 1 else .6,
        oof_train_through=year-1) for year in years for horse in (2, 1)])


def test_backfill_preserves_old_raw_teacher_values_and_row_order():
    old, historical = teacher(range(2019, 2026)), teacher(range(2013, 2019))
    result = M.research.compose_weights(historical, old)
    pd.testing.assert_frame_equal(result[result.year.ge(2019)].reset_index(drop=True), old, check_exact=True)
    pd.testing.assert_frame_equal(result[result.year.le(2018)].reset_index(drop=True), historical, check_exact=True)
    assert result.groupby("race_id").p_oof.sum().gt(1.2).all()


@pytest.mark.parametrize("change", ["future", "duplicate", "missing_year", "date", "probability"])
def test_invalid_teacher_backfill_rejected(change):
    old, historical = teacher(range(2019, 2026)), teacher(range(2013, 2019))
    if change == "future":
        historical.loc[0, "oof_train_through"] = historical.loc[0, "year"]
    elif change == "duplicate":
        historical = pd.concat([historical, historical.iloc[:1]], ignore_index=True)
    elif change == "missing_year":
        historical = historical[historical.year.ne(2013)]
    elif change == "date":
        historical.loc[0, "race_date"] = "2014-01-01"
    else:
        historical.loc[0, "p_oof"] = float("inf")
    with pytest.raises(ValueError, match="Teacher"):
        M.research.compose_weights(historical, old)
