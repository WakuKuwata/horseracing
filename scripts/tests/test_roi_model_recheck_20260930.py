from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pandas as pd
import pytest

spec = spec_from_file_location("recheck", Path(__file__).resolve().parents[1] / "roi_explore/recheck_models_20260930.py")
m = module_from_spec(spec)
spec.loader.exec_module(m)


def test_selection_uses_no_outcome_and_respects_strict_boundaries():
    d = pd.DataFrame(dict(race_id=["a", "a", "b"], horse_id=["1", "2", "3"], pred=[20., 21., 50.], odds=[2., 20., 21.]))
    policy = dict(expected_return_gt=1.2, odds_lt=21, selection="all")
    assert m.select(d, policy).tolist() == [False, True, False]
    d["ret"] = [99999., -100., 99999.]
    assert m.select(d, policy).tolist() == [False, True, False]


def test_top1_is_deterministic_and_does_not_use_results():
    d = pd.DataFrame(dict(race_id=["a", "a", "b"], horse_id=["2", "1", "3"], pred=[30., 30., 20.], odds=[2., 3., 21.]))
    policy = dict(expected_return_gt=1., odds_lt=None, selection="top1")
    assert m.select(d, policy).tolist() == [False, True, True]


def test_accounting_aggregates_total_return_not_average_race_roi():
    d = pd.DataFrame(dict(race_id=["a", "a", "b", "c"], race_date=["2020-01-01", "2020-01-01", "2020-01-02", "2020-01-03"], ret=[200., -100., -100., -100.]))
    s = m.metrics(d, 4)
    assert s["roi"] == .75
    assert s["profit_yen"] == -100.
    assert s["skip_rate"] == .25
    assert s["max_daily_drawdown_yen"] == 200.
    assert s["roi_without_largest_hit"] == 0.


def test_no_bets_is_undefined_roi():
    assert m.metrics(pd.DataFrame(), 10)["roi"] is None


def test_catalog_count_and_primary_are_fixed():
    ps = m.policies()
    assert len(ps) == 48
    assert sum(p["primary"] for p in ps) == 2
    assert len({p["policy_id"] for p in ps}) == 48


def test_bootstrap_is_ratio_of_daily_totals():
    d = pd.DataFrame(dict(race_date=["2020-01-01", "2020-01-01", "2020-01-02"], ret=[0., 0., 0.]))
    ci = m.bootstrap(d, repetitions=100)
    assert ci["lo"] == pytest.approx(1.)
    assert ci["hi"] == pytest.approx(1.)
