"""Causal chronology, nested comparison, and fixed-budget accounting for research151."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / "roi_explore/roi_prediction_ablation_151/analysis.py"
SPEC = importlib.util.spec_from_file_location("roi151_test_analysis", PATH)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def sample(years=range(2020, 2027), races=5, horses=4):
    rng = np.random.default_rng(151)
    rows = []
    for year in years:
        for race in range(races):
            values = rng.dirichlet(np.ones(horses), size=3)
            win = int(rng.choice(horses, p=.65*values[0]+.35*values[2]))
            for h in range(horses):
                rows.append(dict(race_id=f"{year}010101{race+1:02d}", horse_id=str(h+1),
                    horse_number=h+1, race_date=f"{year}-01-{race+1:02d}", year=year,
                    field_size=horses, won=h == win, odds=1/(1.2*values[0,h]),
                    q=values[0,h], p_mev=values[1,h], p_pred_preweight=values[2,h],
                    p_pred_full=.5*values[0,h]+.5*values[2,h]))
    return pd.DataFrame(rows)


def test_analytic_gradient_and_nested_zero_coefficient():
    d = sample(years=[2020])
    starts, ridx = m.group_structure(d)
    x = np.log(d[["q", "p_mev", "p_pred_preweight"]].to_numpy())
    wins = np.flatnonzero(d.won)
    theta = np.array([.6, .4, .2])
    _, grad = m.objective(theta, x, starts, ridx, wins)
    delta = 1e-6
    numeric = []
    for i in range(3):
        direction = np.eye(3)[i]*delta
        numeric.append((m.objective(theta+direction,x,starts,ridx,wins)[0]-
            m.objective(theta-direction,x,starts,ridx,wins)[0])/(2*delta))
    np.testing.assert_allclose(grad, numeric, atol=1e-8)
    reduced = m.predict_stack(d, ["q", "p_mev"], [.7,.3])
    nested = m.predict_stack(d, ["q", "p_mev", "p_pred_preweight"], [.7,.3,0.])
    np.testing.assert_allclose(reduced, nested, atol=1e-15)


def test_stack_probabilities_preserve_full_field_mass_and_row_permutation():
    d = sample(years=[2020])
    columns = ["q", "p_mev", "p_pred_preweight"]
    p = m.predict_stack(d, columns, [.4,.4,.2])
    np.testing.assert_allclose(pd.Series(p).groupby(d.race_id).sum(), 1., atol=1e-14)
    permuted = d.sort_values(["race_id", "horse_number"], ascending=[True,False]).reset_index(drop=True)
    reordered = m.predict_stack(permuted, columns, [.4,.4,.2])
    expected = pd.Series(p, index=pd.MultiIndex.from_frame(d[["race_id","horse_id"]]))
    np.testing.assert_allclose(reordered, expected.loc[pd.MultiIndex.from_frame(permuted[["race_id","horse_id"]])])


def test_future_labels_cannot_affect_same_year_coefficients_or_predictions():
    d = sample()
    original, fits = m.annual_predictions(d)
    changed = d.copy()
    chosen = changed.year.eq(2022)
    changed.loc[chosen,"won"] = np.roll(changed.loc[chosen,"won"].to_numpy().reshape(-1,4),1,axis=1).ravel()
    altered, altered_fits = m.annual_predictions(changed)
    assert fits["2022"] == altered_fits["2022"]
    columns = list(m.STACKS)
    np.testing.assert_array_equal(original.loc[original.year.eq(2022),columns],
                                  altered.loc[altered.year.eq(2022),columns])
    for year, records in fits.items():
        for record in records.values():
            assert record["fit_through"] < f"{year}-01-01"


def test_incomplete_field_and_duplicate_identity_rejected():
    d = sample(years=[2020])
    with pytest.raises(ValueError, match="Incomplete race field"):
        m.validate(d.iloc[1:])
    with pytest.raises(ValueError, match="Duplicate starter identity"):
        m.validate(pd.concat([d,d.iloc[:1]],ignore_index=True))


def test_strict_threshold_flat100_and_cap10_are_outcome_independent():
    d = sample(years=[2020],races=1,horses=12)
    d["odds"] = 12.
    p = np.full(12,1/12)
    assert not m.select_tickets(d,p,1.).any()
    d["odds"] = 13.
    mask = m.select_tickets(d,p,1.)
    assert mask.sum() == 10
    assert d.loc[mask,"horse_number"].tolist() == list(range(1,11))
    changed = d.copy()
    changed["won"] = ~changed.won
    np.testing.assert_array_equal(mask,m.select_tickets(changed,p,1.))
    d["prob"] = p
    result, races, tickets = m.score(d,"prob",1.)
    assert result["policy"]["stake_yen"] == 1000
    assert tickets.stake_yen.eq(100).all()
    assert races.budget_yen.eq(1000).all()


def test_fixed_stake_not_budget_redistribution_and_no_bets_remain():
    d = sample(years=[2020],races=2)
    d["prob"] = np.tile([.4,.3,.2,.1],2)
    d["odds"] = [4.,5.,2.,3.,1.,1.,1.,1.]
    result,races,tickets = m.score(d,"prob",1.)
    assert len(tickets) == 2
    assert tickets.stake_yen.eq(100).all()
    assert len(races) == 2
    assert races.stake_yen.tolist() == [200,0]
    assert result["policy"]["cash_yen"] == 1800
    assert result["policy"]["skip_fraction"] == .5


def test_paired_roi_recomputes_ratios_and_uses_common_dates():
    base = pd.DataFrame(dict(race_id=["202001010101","202001010102"],
        race_date=["2020-01-01","2020-01-02"],budget_yen=[1000,1000],
        stake_yen=[100,900],payout_yen=[200,800],profit_yen=[100,-100],winner_nll=[1.,2.]))
    new = base.copy()
    new["stake_yen"] = [400,100]
    new["payout_yen"] = [300,300]
    new["profit_yen"] = new.payout_yen-new.stake_yen
    tables = {"roi_only_ev1":base,"plus_prediction_ev1":new}
    contrast = [["plus_prediction_ev1","roi_only_ev1"]]
    comparison = m.paired(tables,contrast,repetitions=1000,seed=2)["plus_prediction_ev1_minus_roi_only_ev1"]
    assert comparison["deployed_roi"]["difference"] == pytest.approx(.2)
    draws = np.random.default_rng(2).multinomial(2,[.5,.5],size=1000)
    roi = (draws@new.payout_yen)/(draws@new.stake_yen)-(draws@base.payout_yen)/(draws@base.stake_yen)
    np.testing.assert_allclose(comparison["deployed_roi"]["adjusted95_ci"],m.interval(roi,.05/3))
    tables["plus_prediction_ev1"] = new.iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError,match="Paired populations differ"):
        m.paired(tables,contrast,repetitions=10)


def test_verdict_does_not_hide_profit_deterioration_behind_better_nll():
    record = {metric:dict(adjusted95_ci=value) for metric,value in dict(
        profit_per_budget=[-.03,-.01],deployed_roi=[-.1,.1],winner_nll=[-.01,-.001]).items()}
    result = m.verdict({"plus_prediction_ev1_minus_roi_only_ev1":record})
    assert result["status"] == "historical_primary_profit_deterioration"
    assert not result["historical_roi_support"]
    assert result["prospective_retention_evidence_required"]
    assert not result["can_adopt"]


def test_no_purchase_roi_is_undefined_and_cannot_justify_retention():
    base = pd.DataFrame(dict(race_id=["202001010101"],race_date=["2020-01-01"],
        budget_yen=[1000],stake_yen=[0],payout_yen=[0],profit_yen=[0],winner_nll=[1.]))
    tables = {"roi_only_ev1":base,"plus_prediction_ev1":base.copy()}
    comparison = m.paired(tables,[["plus_prediction_ev1","roi_only_ev1"]])
    assert comparison["plus_prediction_ev1_minus_roi_only_ev1"]["deployed_roi"]["ci95"] == [None,None]
    assert m.verdict(comparison)["status"] == "insufficient_defined_bootstrap_replicates"


def test_correction_history_uses_only_distinct_strictly_prior_started_days():
    loader = m.load_inputs_module()
    d = pd.DataFrame(dict(race_id=["201901010101","201901010102","202001010101","202001010102","202101010101"],
        horse_id=["h"]*5,race_date=["2019-01-01","2019-02-01","2020-01-01","2020-01-01","2021-01-01"],
        days_since_last=[np.nan,31.,334.,334.,366.],sex=["牝"]*5))
    original = loader.correction_terms(d)
    for value in original.loc[original.race_date.eq("2020-01-01"),"prior_gap_log"]:
        assert value == pytest.approx(np.log1p(31.))
    changed = d.copy()
    changed.loc[4,"race_date"] = "2022-05-01"
    changed.loc[4,"days_since_last"] = 851.
    altered = loader.correction_terms(changed)
    np.testing.assert_array_equal(original.loc[original.race_date.eq("2020-01-01"),"prior_gap_log"],
        altered.loc[altered.race_date.eq("2020-01-01"),"prior_gap_log"])


def test_zero_correction_preserves_win_and_centered_logp_temperature():
    loader = m.load_inputs_module()
    p = np.array([.5,.3,.2])
    terms = np.array([[np.nan,1.,0.,0.],[1.,np.nan,0.,0.],[2.,2.,0.,0.]])
    np.testing.assert_allclose(loader.correct_win(p,terms,[0.],joint=False),p,atol=1e-15)
    actual = loader.correct_win(p,terms,[0.,0.,0.,0.,.5],joint=True)
    expected = p**1.5 / (p**1.5).sum()
    np.testing.assert_allclose(actual,expected,atol=1e-15)
