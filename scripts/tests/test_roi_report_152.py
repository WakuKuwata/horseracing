"""Four planned contrasts preserve the comparator, cash and race calendar."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_152/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi_report152", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CONTRASTS=[["normalized_pace",b] for b in ("baseline","weighted_baseline","normalized_weighted_baseline","pace")]


def paired(tables,contrasts,repetitions):
    return M.common.paired(tables,contrasts,repetitions=repetitions,family_size=4)


def table():
    return pd.DataFrame(dict(race_id=["202001010101", "202001010102"], race_date=["2020-01-01", "2020-01-02"],
        stake_yen=[1000., 0.], payout_yen=[3000., 0.], profit_yen=[2000., 0.], budget_yen=[1000, 1000], winner_nll=[1., 2.]))


def test_four_identical_contrasts_zero_with_no_bet_day():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "normalized_pace", "normalized_weighted_baseline", "pace")}
    result = paired(frames, CONTRASTS, repetitions=60)
    assert result["family_size_per_metric"] == 4
    assert len(result["results"]) == 4
    for rec in result["results"].values():
        for metric in rec.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_comparator_changes_profit_but_does_not_force_equal_stakes():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "normalized_pace", "normalized_weighted_baseline", "pace")}
    for c in ("stake_yen", "payout_yen", "profit_yen"):
        frames["weighted_baseline"][c] /= 2
    result = paired(frames, CONTRASTS, repetitions=20)["results"]
    assert result["normalized_pace_minus_baseline"]["profit_per_budget"]["difference"] == 0
    assert result["normalized_pace_minus_weighted_baseline"]["profit_per_budget"]["difference"] == .5
    assert result["normalized_pace_minus_weighted_baseline"]["deployed_roi"]["difference"] == 0


def test_unmatched_nonbet_day_and_incorrect_family_rejected():
    frames = {k: table() for k in ("baseline", "weighted_baseline", "normalized_pace", "normalized_weighted_baseline", "pace")}
    frames["normalized_pace"] = frames["normalized_pace"].iloc[:1]
    with pytest.raises(ValueError, match="populations"):
        paired(frames, CONTRASTS, repetitions=5)
    frames["normalized_pace"] = table()
    with pytest.raises(ValueError, match="family"):
        paired(frames, CONTRASTS[:2], repetitions=5)


def test_same_race_deleted_from_both_when_measuring_influence():
    reference = table()
    reference["race_id"] = ["202608030111", "202601010101"]
    reference["race_date"] = ["2026-04-25", "2026-01-01"]
    new = reference.copy()
    new["profit_yen"] += [100, -40]
    result = M.common.sensitivity({"baseline": reference, "normalized_pace": new}, [["normalized_pace", "baseline"]])
    rec = result["normalized_pace_minus_baseline"]
    assert rec["known_race_delta_yen"] == 100
    assert rec["without_known_race_delta_yen"] == -40
    assert rec["without_top_difference_yen"] == -40
    assert rec["same_calendar_jan1_jun21_by_year"] == {"2026": 60}


def parent():
    return pd.DataFrame(dict(race_id=["202001010101"]*2, horse_id=["horse1", "horse2"],
        horse_number=[1, 2], race_date=["2020-01-01"]*2, year=[2020]*2,
        odds=[2., 4.], won=[1, 0], field_size=[2, 2], p=[.8, .6]))


def test_normalization_uses_all_starters_and_preserves_input():
    a = parent()
    before = a.copy()
    actual = M.normalize(a)
    assert actual.p.tolist() == pytest.approx([4/7, 3/7])
    pd.testing.assert_frame_equal(a, before)
    pd.testing.assert_frame_equal(actual[M.models.PRED_KEYS], a[M.models.PRED_KEYS])
    assert actual.p.sum() == pytest.approx(1)
    # Candidate-only denominator (.6) would incorrectly make the second value 1.
    assert actual.p.iloc[1] < 1


def test_independent_races_preserve_row_order_and_index():
    a = parent()
    b = parent()
    b["race_id"] = "202001010102"
    b["p"] = [.1, .3]
    both = pd.concat([a,b]).iloc[[3,0,2,1]]
    actual = M.normalize(both)
    assert actual.p.tolist() == pytest.approx([.75, 4/7, .25, 3/7])
    assert actual.index.equals(both.index)
    assert actual.groupby("race_id").p.sum().tolist() == pytest.approx([1,1])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.01, 1.01])
def test_invalid_parent_probability_rejected(value):
    a = parent()
    a.loc[0, "p"] = value
    with pytest.raises(ValueError, match="probabilities"):
        M.normalize(a)


def test_invalid_zero_sum_duplicate_empty_incomplete_fields_rejected():
    a = parent()
    zero = a.copy()
    zero["p"] = 0.
    bad_size = a.copy()
    bad_size.loc[0, "field_size"] = 3
    bad_number = a.copy()
    bad_number["horse_number"] = 1
    for bad in (a.iloc[:0], pd.concat([a,a]), a.iloc[:1], zero, bad_size, bad_number):
        with pytest.raises(ValueError):
            M.normalize(bad)


def test_denominator_does_not_depend_on_odds_or_outcomes():
    a, b = parent(), parent()
    b["odds"] = [99., 1.1]
    b["won"] = [0,1]
    assert M.normalize(a).p.tolist() == M.normalize(b).p.tolist()


def test_mean_raw_seeds_first_then_normalize():
    a = parent()
    first, second = [.8, .2], [.1, .3]
    a["p"] = [(x+y)/2 for x,y in zip(first,second)]
    actual = M.normalize(a)
    assert actual.p.tolist() == pytest.approx([.45/.7, .25/.7])
    assert actual.p.iloc[0] != pytest.approx((.8/1 + .1/.4)/2)


def test_winner_nll_invariant_but_purchases_change():
    a = parent()
    original, _, raw_bets = M.common.prior.score(a)
    transformed, _, normalized_bets = M.common.prior.score(M.normalize(a))
    assert transformed["quality"]["winner_nll"] == pytest.approx(original["quality"]["winner_nll"], abs=1e-14)
    assert raw_bets.horse_number.tolist() == [1,2]
    assert normalized_bets.horse_number.tolist() == [2]
    assert transformed["policy"]["stake_yen"] == 1000


def test_normalization_can_increase_ev_in_low_mass_race():
    a = parent()
    a["p"] = [.3, .2]
    raw, _, _ = M.common.prior.score(a)
    norm, _, bets = M.common.prior.score(M.normalize(a))
    assert raw["policy"]["n_bets"] == 0
    assert norm["policy"]["n_bets"] == 1
    assert bets.horse_number.tolist() == [2]


def test_exact_requested_unweighted_pace_contract():
    recipe=M.research.NORMALIZATION
    assert recipe["parent"]=="pace" and recipe["parent_features"]==91
    assert recipe["parent_weights"] is None
    assert recipe["candidate"]=="normalized_pace"
    assert recipe["before_ev"] is True and recipe["fitted_parameters"]==0
    assert M.research.CONTROLS==["baseline","weighted_baseline","normalized_weighted_baseline","pace"]
    assert M.research.CONTRASTS==CONTRASTS
