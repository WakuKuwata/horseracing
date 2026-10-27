"""Three planned contrasts preserve the comparator, cash and race calendar."""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_149/report.py"
SPEC = importlib.util.spec_from_file_location("test_roi_report149", PATH)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CANDIDATE="half_normalized_weighted_baseline"
CONTROLS=("baseline", "weighted_baseline", "normalized_weighted_baseline")
CONTRASTS=[[CANDIDATE, b] for b in CONTROLS]


def paired(frames, contrasts, repetitions):
    return M.common.paired(frames, contrasts, repetitions=repetitions, family_size=3)


def table():
    return pd.DataFrame(dict(race_id=["202001010101", "202001010102"], race_date=["2020-01-01", "2020-01-02"],
        stake_yen=[1000., 0.], payout_yen=[3000., 0.], profit_yen=[2000., 0.], budget_yen=[1000, 1000], winner_nll=[1., 2.]))


def test_three_identical_contrasts_zero_with_no_bet_day():
    frames = {k: table() for k in (*CONTROLS,CANDIDATE)}
    result = paired(frames, CONTRASTS, repetitions=60)
    assert result["family_size_per_metric"] == 3
    assert len(result["results"]) == 3
    for rec in result["results"].values():
        for metric in rec.values():
            assert metric["difference"] == 0
            assert metric["bonferroni_familywise95_ci"] == [0, 0]


def test_comparator_changes_profit_but_does_not_force_equal_stakes():
    frames = {k: table() for k in (*CONTROLS,CANDIDATE)}
    for c in ("stake_yen", "payout_yen", "profit_yen"):
        frames["weighted_baseline"][c] /= 2
    result = paired(frames, CONTRASTS, repetitions=20)["results"]
    assert result["half_normalized_weighted_baseline_minus_baseline"]["profit_per_budget"]["difference"] == 0
    assert result["half_normalized_weighted_baseline_minus_weighted_baseline"]["profit_per_budget"]["difference"] == .5
    assert result["half_normalized_weighted_baseline_minus_weighted_baseline"]["deployed_roi"]["difference"] == 0


def test_unmatched_nonbet_day_and_incorrect_family_rejected():
    frames = {k: table() for k in (*CONTROLS,CANDIDATE)}
    frames["half_normalized_weighted_baseline"] = frames["half_normalized_weighted_baseline"].iloc[:1]
    with pytest.raises(ValueError, match="populations"):
        paired(frames, CONTRASTS, repetitions=5)
    frames["half_normalized_weighted_baseline"] = table()
    with pytest.raises(ValueError, match="family"):
        paired(frames, CONTRASTS[:2], repetitions=5)


def test_same_race_deleted_from_both_when_measuring_influence():
    reference = table()
    reference["race_id"] = ["202608030111", "202601010101"]
    reference["race_date"] = ["2026-04-25", "2026-01-01"]
    new = reference.copy()
    new["profit_yen"] += [100, -40]
    result = M.common.sensitivity({"baseline": reference, "half_normalized_weighted_baseline": new}, [["half_normalized_weighted_baseline", "baseline"]])
    rec = result["half_normalized_weighted_baseline_minus_baseline"]
    assert rec["known_race_delta_yen"] == 100
    assert rec["without_known_race_delta_yen"] == -40
    assert rec["without_top_difference_yen"] == -40
    assert rec["same_calendar_jan1_jun21_by_year"] == {"2026": 60}


def parent():
    return pd.DataFrame(dict(race_id=["202001010101"]*2, horse_id=["horse1", "horse2"],
        horse_number=[1,2],race_date=["2020-01-01"]*2,year=[2020]*2,
        odds=[2.,4.],won=[1,0],field_size=[2,2],p=[.8,.6]))


def test_fixed_half_uses_all_starters_and_preserves_parent():
    a=parent()
    before=a.copy()
    actual=M.half_correct(a)
    assert actual.p.tolist()==pytest.approx([.8/(1.4**.5),.6/(1.4**.5)])
    assert actual.p.sum()==pytest.approx(1.4**.5)
    assert actual.p.sum()!=pytest.approx(1.)
    pd.testing.assert_frame_equal(a,before)
    pd.testing.assert_frame_equal(actual[M.models.PRED_KEYS],a[M.models.PRED_KEYS])


def test_multiple_races_preserve_order_and_index():
    a,b=parent(),parent()
    b["race_id"]="202001010102"
    b["p"]=[.1,.3]
    both=pd.concat([a,b]).iloc[[3,0,2,1]]
    actual=M.half_correct(both)
    assert actual.p.tolist()==pytest.approx([.3/.4**.5,.8/1.4**.5,.1/.4**.5,.6/1.4**.5])
    assert actual.index.equals(both.index)


@pytest.mark.parametrize("value",[float("nan"),float("inf"),-.01,1.01])
def test_invalid_parent_probability_rejected(value):
    a=parent()
    a.loc[0,"p"]=value
    with pytest.raises(ValueError,match="probabilities"):
        M.half_correct(a)


def test_empty_duplicate_zero_sum_incomplete_fields_rejected():
    a=parent()
    zero=a.copy()
    zero["p"]=0.
    bad_size=a.copy()
    bad_size.loc[0,"field_size"]=3
    bad_number=a.copy()
    bad_number["horse_number"]=1
    for bad in (a.iloc[:0],pd.concat([a,a]),a.iloc[:1],zero,bad_size,bad_number):
        with pytest.raises(ValueError):
            M.half_correct(bad)


def test_correction_does_not_depend_on_odds_or_outcomes():
    a,b=parent(),parent()
    b["odds"]=[99.,1.1]
    b["won"]=[0,1]
    assert M.half_correct(a).p.tolist()==M.half_correct(b).p.tolist()


def test_seed_mean_precedes_correction():
    a=parent()
    a["p"]=[.45,.25]
    actual=M.half_correct(a)
    assert actual.p.tolist()==pytest.approx([.45/.7**.5,.25/.7**.5])
    assert actual.p.iloc[0]!=pytest.approx((.8/1.**.5+.1/.4**.5)/2)


def test_nll_invariant_but_purchase_selection_changes():
    a=parent()
    a["odds"]=[1.6,4.]
    raw,_,old_bets=M.common.prior.score(a)
    half,_,new_bets=M.common.prior.score(M.half_correct(a))
    assert half["quality"]["winner_nll"]==pytest.approx(raw["quality"]["winner_nll"],abs=1e-14)
    assert old_bets.horse_number.tolist()==[1,2]
    assert new_bets.horse_number.tolist()==[2]
    assert half["policy"]["stake_yen"]==1000


def test_low_mass_correction_increases_ev_without_full_normalization():
    a=parent()
    a["p"]=[.3,.2]
    a["odds"]=[2.,5.]
    raw,_,_=M.common.prior.score(a)
    half,_,bets=M.common.prior.score(M.half_correct(a))
    assert raw["policy"]["n_bets"]==0
    assert half["policy"]["n_bets"]==1
    assert bets.horse_number.tolist()==[2]
    assert M.half_correct(a).p.sum()==pytest.approx(.5**.5)
