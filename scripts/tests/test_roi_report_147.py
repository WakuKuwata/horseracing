"""Three planned contrasts preserve the comparator, cash and race calendar."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_147/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi_report147", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CONTRASTS = [["half_blend", b] for b in ("baseline", "weighted_baseline", "pace")]


def table():
    return pd.DataFrame(dict(race_id=["202001010101", "202001010102"], race_date=["2020-01-01", "2020-01-02"],
        stake_yen=[1000., 0.], payout_yen=[3000., 0.], profit_yen=[2000., 0.], budget_yen=[1000, 1000], winner_nll=[1., 2.]))


def test_three_identical_contrasts_zero_with_no_bet_day():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "half_blend", "pace")}
    result = M.paired(frames, CONTRASTS, repetitions=60)
    assert result["family_size_per_metric"] == 3
    assert len(result["results"]) == 3
    for rec in result["results"].values():
        for metric in rec.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_comparator_changes_profit_but_does_not_force_equal_stakes():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "half_blend", "pace")}
    for c in ("stake_yen", "payout_yen", "profit_yen"):
        frames["weighted_baseline"][c] /= 2
    result = M.paired(frames, CONTRASTS, repetitions=20)["results"]
    assert result["half_blend_minus_baseline"]["profit_per_budget"]["difference"] == 0
    assert result["half_blend_minus_weighted_baseline"]["profit_per_budget"]["difference"] == .5
    assert result["half_blend_minus_weighted_baseline"]["deployed_roi"]["difference"] == 0


def test_unmatched_nonbet_day_and_incorrect_family_rejected():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "half_blend", "pace")}
    frames["half_blend"] = frames["half_blend"].iloc[:1]
    with pytest.raises(ValueError, match="populations"):
        M.paired(frames, CONTRASTS, repetitions=5)
    frames["half_blend"] = table()
    with pytest.raises(ValueError, match="family"):
        M.paired(frames, CONTRASTS[:2], repetitions=5)


def test_same_race_deleted_from_both_when_measuring_influence():
    reference = table()
    reference["race_id"] = ["202608030111", "202601010101"]
    reference["race_date"] = ["2026-04-25", "2026-01-01"]
    new = reference.copy()
    new["profit_yen"] += [100, -40]
    result = M.sensitivity({"baseline": reference, "half_blend": new}, [["half_blend", "baseline"]])
    rec = result["half_blend_minus_baseline"]
    assert rec["known_race_delta_yen"] == 100
    assert rec["without_known_race_delta_yen"] == -40
    assert rec["without_top_difference_yen"] == -40
    assert rec["same_calendar_jan1_jun21_by_year"] == {"2026": 60}


def parents():
    frame = pd.DataFrame(dict(race_id=["202001010101"]*2, horse_id=["horse1", "horse2"],
        horse_number=[1, 2], race_date=["2020-01-01"]*2, year=[2020]*2,
        odds=[2., 4.], won=[1, 0], field_size=[2, 2], p=[.8, .6]))
    pace = frame.copy()
    pace["p"] = [.6, .8]
    return frame, pace


def test_half_blend_is_raw_and_does_not_modify_parents():
    a, b = parents()
    before_a, before_b = a.copy(), b.copy()
    mixed = M.half_blend(a, b)
    assert mixed.p.tolist() == pytest.approx([.7, .7])
    assert mixed.p.sum() == pytest.approx(1.4)
    pd.testing.assert_frame_equal(mixed[M.models.PRED_KEYS], a[M.models.PRED_KEYS])
    pd.testing.assert_frame_equal(a, before_a)
    pd.testing.assert_frame_equal(b, before_b)


@pytest.mark.parametrize("column,value", [("odds", 3.), ("won", 0), ("horse_id", "other")])
def test_parent_metadata_mismatch_rejected(column, value):
    a, b = parents()
    b.loc[0, column] = value
    with pytest.raises(AssertionError):
        M.half_blend(a, b)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.01, 1.01])
def test_invalid_parent_probability_rejected(value):
    a, b = parents()
    b.loc[0, "p"] = value
    with pytest.raises(ValueError, match="probabilities"):
        M.half_blend(a, b)


def test_duplicate_and_empty_population_rejected():
    a, b = parents()
    for left, right in [(a.iloc[:0], b.iloc[:0]),
                        (pd.concat([a, a]), pd.concat([b, b]))]:
        with pytest.raises(ValueError, match="population"):
            M.half_blend(left, right)


def test_blend_happens_before_purchase_threshold():
    a, b = parents()
    a["p"] = [.55, .1]
    b["p"] = [.75, .1]
    score, _, bets = M.prior.score(M.half_blend(a, b))
    assert score["policy"]["stake_yen"] == 1000
    assert bets.horse_number.tolist() == [1]
    assert bets.p_raw.tolist() == pytest.approx([.65])
