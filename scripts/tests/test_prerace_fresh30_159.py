"""Freshness is measured at decision time, inclusively, on a complete common field."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "fresh159_test", ROOT / "scripts/roi_explore/prerace_fresh30_159/run.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
DECISION = pd.Timestamp("2026-10-18T03:00:00Z")


def field(age="30min", rid="202605040101"):
    return pd.DataFrame([dict(race_id=rid, horse_id=f"h{n}", horse_number=n, field_size=2,
        race_date="2026-10-18", quote_observed_at=DECISION - pd.Timedelta(age),
        decision_at=DECISION, post_time=DECISION + pd.Timedelta("3h"),
        judgment_odds=odds, q=q, p_mev=p, p_pred_preweight=p)
        for n, odds, q, p in [(1, 3., .6, .7), (2, 4.5, .4, .3)]])


@pytest.mark.parametrize("age,expected", [
    ("0s", "accepted"), ("1ns", "accepted"), ("1800s", "accepted"),
    ("1800000000001ns", "quote_older_than_30min"),
    ("1800.000001s", "quote_older_than_30min"),
    ("31min", "quote_older_than_30min"), ("-1ns", "quote_after_decision"),
])
def test_exact_inclusive_time_boundary(age, expected):
    selected, ledger = runner.freshness_filter(field(age))
    assert ledger.reason.tolist() == [expected]
    assert len(selected) == (2 if expected == "accepted" else 0)


def test_measurement_is_to_decision_not_to_post_time_or_cached_age():
    d = field("10min")
    d["post_time"] = DECISION + pd.Timedelta("20h")
    d["quote_age_seconds"] = 999999
    selected, ledger = runner.freshness_filter(d)
    assert len(selected) == 2 and ledger.quote_age_seconds.iloc[0] == 600
    assert selected.quote_age_seconds.eq(600).all()


@pytest.mark.parametrize("column", ["quote_observed_at", "decision_at", "post_time"])
def test_unknown_time_excludes_whole_race(column):
    d = field()
    d.loc[0, column] = pd.NaT
    selected, ledger = runner.freshness_filter(d)
    assert selected.empty and ledger.reason.tolist() == ["missing_time"]


@pytest.mark.parametrize("column", ["quote_observed_at", "decision_at", "post_time"])
def test_naive_time_is_not_guessed_to_be_utc(column):
    d = field()
    d[column] = d[column].dt.tz_localize(None)
    selected, ledger = runner.freshness_filter(d)
    assert selected.empty and ledger.reason.tolist() == ["ambiguous_time"]


def test_timezone_offsets_representing_the_same_times_preserve_freshness():
    d = field("30min")
    for column in ["quote_observed_at", "decision_at", "post_time"]:
        d[column] = d[column].dt.tz_convert("Asia/Tokyo")
    selected, ledger = runner.freshness_filter(d)
    assert len(selected) == 2 and ledger.quote_age_seconds.iloc[0] == 1800


@pytest.mark.parametrize("column", ["quote_observed_at", "decision_at", "post_time"])
def test_mixed_observation_does_not_keep_a_horse_only_subset(column):
    d = field()
    d.loc[0, column] += pd.Timedelta("1s")
    selected, ledger = runner.freshness_filter(d)
    assert selected.empty and ledger.reason.tolist() == ["mixed_race_time"]


@pytest.mark.parametrize("offset", ["0s", "-1s"])
def test_decision_must_be_strictly_before_post(offset):
    d = field()
    d["post_time"] = DECISION + pd.Timedelta(offset)
    selected, ledger = runner.freshness_filter(d)
    assert selected.empty and ledger.reason.tolist() == ["decision_not_prestart"]


@pytest.mark.parametrize("defect", ["duplicate_id", "duplicate_number", "missing_horse", "mixed_date"])
def test_invalid_whole_field_is_rejected(defect):
    d = field()
    if defect == "duplicate_id":
        d.loc[1, "horse_id"] = "h1"
    elif defect == "duplicate_number":
        d.loc[1, "horse_number"] = 1
    elif defect == "missing_horse":
        d = d.iloc[:1]
    else:
        d.loc[1, "race_date"] = "2026-10-19"
    with pytest.raises(ValueError):
        runner.freshness_filter(d)


def test_results_and_final_prices_do_not_select_a_more_favorable_race():
    d = pd.concat([field("10min"), field("40min", "202605040102")], ignore_index=True)
    before = d.copy(deep=True)
    selected, ledger = runner.freshness_filter(d)
    pd.testing.assert_frame_equal(d, before)
    d["won"] = [False, True, True, True]
    d["final_odds"] = [1., 9999., 9999., 9999.]
    d["official_payout_yen"] = [0, 100000, 1000000, 1000000]
    again, new_ledger = runner.freshness_filter(d)
    assert set(selected.race_id) == set(again.race_id) == {"202605040101"}
    pd.testing.assert_frame_equal(ledger, new_ledger)


def test_empty_population_is_preserved_as_empty():
    d = field().iloc[:0]
    selected, ledger = runner.freshness_filter(d)
    assert selected.empty and ledger.empty and "reason" in ledger


def test_fresh_subset_preserves_parent_decisions_and_uses_official_payout_afterward():
    d = pd.concat([field("10min"), field("40min", "202605040102")], ignore_index=True)
    coefficients = {
        "roi_only": dict(coefficients={"q": 0., "p_mev": 1.}, fit_through="2025-12-31"),
        "plus_prediction": dict(coefficients={"q": 0., "p_mev": 1., "p_pred_preweight": 0.},
                                fit_through="2025-12-31"),
    }
    all_decisions = runner.parent.decide(d, coefficients)
    selected, _ = runner.freshness_filter(d)
    actual = runner.parent.decide(selected, coefficients)
    expected = all_decisions[all_decisions.race_id.isin(selected.race_id)].reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected)
    labels = pd.DataFrame([dict(race_id="202605040101", horse_id=f"h{n}", won=n == 1,
                               official_payout_yen=850 if n == 1 else 0) for n in [1, 2]])
    _, accounts, _ = runner.parent.settle_all(actual, labels)
    assert accounts["roi_only"].payout.sum() == 850
    assert accounts["plus_prediction"].payout.sum() == 850
    assert not {"won", "official_payout_yen", "final_odds"} & set(actual.columns)
