"""A stored decision must precede the race and never obtain prices from settlement data."""
from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


inputs = _load("inputs158_test", ROOT / "scripts/roi_explore/prerace_compare_158/inputs.py")
evaluation = _load("ev158_test", ROOT / "scripts/roi_explore/verified_recompare_157/evaluation.py")
runner = _load("run158_test", ROOT / "scripts/roi_explore/prerace_compare_158/run.py")
RID = "202608040712"
MANIFEST_SHA = "b" * 64
BUNDLE_SHA = "a" * 64
POST = pd.Timestamp("2026-10-18T06:00:00Z")
DECISION = pd.Timestamp("2026-10-18T05:00:00Z")
OBSERVED = pd.Timestamp("2026-10-18T04:50:00Z")
OLD_TIME = pd.Timestamp("2026-10-18T04:55:00Z")


def _race():
    return dict(race_id=RID, race_date="2026-10-18", post_time=POST, track_type="芝")


def _entries():
    return pd.DataFrame([dict(race_id=RID, horse_id=f"h{n}", horse_number=n,
                              entry_status="started") for n in range(1, 4)])


def _results():
    return pd.DataFrame([dict(race_id=RID, horse_id=f"h{n}", finish_order=n,
                              result_status="finished") for n in range(1, 4)])


def _payouts():
    return pd.DataFrame([dict(race_id=RID, horse_number=1, payout_yen=850)])


def _mev():
    return pd.DataFrame([dict(
        race_id=RID, horse_id=f"h{n}", horse_number=n, model_version="mev-ens15-v1",
        win_prob=str(p), odds_used=str(o), expected_return=str(Decimal(str(p)) * Decimal(str(o))),
        odds_observed_at=OBSERVED, result_pending_at_compute=True,
        logic_version="mev-ens15-v1;seeds=1-15;threads=1;deterministic;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007;oddsobs=quote", run_id="mev-run",
        computed_at=DECISION, booster="ensemble_2026.json", booster_sha256=MANIFEST_SHA,
    ) for n, p, o in [(1, .45, 2.), (2, .35, 5.), (3, .20, 12.)]])


def _legacy():
    run = dict(prediction_run_id="old-run", race_id=RID, model_version="mix-129-nj6",
               logic_version=f"feat=features-021;mix=6:{BUNDLE_SHA[:12]};coef=2026;wregime=serving",
               computed_at=OLD_TIME, created_at=OLD_TIME, updated_at=OLD_TIME)
    predictions = pd.DataFrame([dict(
        prediction_run_id="old-run", horse_id=f"h{n}", win_prob=str(p), top2_prob=str(min(1., 2*p)),
        top3_prob="1.0", created_at=OLD_TIME, updated_at=OLD_TIME,
    ) for n, p in [(1, .4), (2, .35), (3, .25)]])
    features = pd.DataFrame([dict(
        prediction_run_id="old-run", horse_id=f"h{n}", feature_version="features-021",
        features_json=json.dumps(dict(weight=None, weight_diff=None, carried_weight_ratio=None)),
        created_at=OLD_TIME, updated_at=OLD_TIME,
    ) for n in range(1, 4)])
    return run, predictions, features


def _mev_flags(group=None, entries=None, race=None):
    return inputs.mev_flags(_mev() if group is None else group,
                            _entries() if entries is None else entries,
                            _race() if race is None else race,
                            manifest_sha=MANIFEST_SHA)


def _legacy_flags(run=None, predictions=None, features=None, entries=None, race=None,
                  decision=DECISION):
    a, b, c = _legacy()
    return inputs.legacy_flags(
        a if run is None else run, b if predictions is None else predictions,
        c if features is None else features, _entries() if entries is None else entries,
        _race() if race is None else race, decision=decision, bundle_sha=BUNDLE_SHA,
        train_through="2026-08-23", coefficient_train_through="2025-12-31")


def test_complete_prestart_mev_is_eligible_without_current_popularity_or_odds():
    assert _mev_flags() == []


@pytest.mark.parametrize("column,value", [
    ("odds_observed_at", DECISION + pd.Timedelta(microseconds=1)),
    ("odds_observed_at", pd.NaT),
    ("computed_at", POST),
    ("computed_at", POST + pd.Timedelta(seconds=1)),
    ("computed_at", pd.NaT),
    ("result_pending_at_compute", False),
    ("logic_version", "mev-ens15-v1;oddsobs=proxy"),
    ("logic_version", "mev-ens15-v1"),
    ("logic_version", "mev-ens15-v1;oddsobs=quote_extra"),
    ("booster_sha256", "c" * 64),
    ("odds_used", "0.9"),
    ("win_prob", "NaN"),
])
def test_mev_rejects_late_unknown_proxy_or_unbound_material(column, value):
    group = _mev()
    group[column] = value
    assert _mev_flags(group)


def test_quote_observed_exactly_at_compute_is_allowed_but_compute_at_post_is_not():
    group = _mev()
    group["odds_observed_at"] = DECISION
    assert _mev_flags(group) == []
    group["computed_at"] = POST
    group["odds_observed_at"] = POST
    assert _mev_flags(group)


@pytest.mark.parametrize("defect", ["missing", "extra", "duplicate", "renumbered", "mixed_run", "mixed_quote_time"])
def test_mev_requires_a_whole_matching_field_and_one_snapshot(defect):
    group = _mev()
    if defect == "missing":
        group = group.iloc[:-1]
    elif defect == "extra":
        group.loc[len(group)] = group.iloc[-1].to_dict() | dict(horse_id="other", horse_number=4)
    elif defect == "duplicate":
        group = pd.concat([group, group.iloc[:1]], ignore_index=True)
    elif defect == "renumbered":
        group.loc[0, "horse_number"] = 3
    elif defect == "mixed_run":
        group.loc[0, "run_id"] = "other-run"
    else:
        group.loc[0, "odds_observed_at"] = OBSERVED - pd.Timedelta(seconds=1)
    assert _mev_flags(group)


def test_entry_duplicate_number_is_not_hidden_by_a_dictionary_join():
    entries = _entries()
    entries.loc[2, "horse_number"] = 2
    assert _mev_flags(entries=entries)


def test_legacy_available_at_decision_and_preweight_is_eligible():
    assert _legacy_flags() == []
    run, predictions, features = _legacy()
    run["computed_at"] = DECISION
    run["created_at"] = DECISION
    run["updated_at"] = DECISION
    predictions[["created_at", "updated_at"]] = DECISION
    features[["created_at", "updated_at"]] = DECISION
    assert _legacy_flags(run, predictions, features) == []


@pytest.mark.parametrize("record,column", [
    ("run", "computed_at"), ("run", "created_at"), ("run", "updated_at"),
    ("predictions", "created_at"), ("predictions", "updated_at"),
    ("features", "created_at"), ("features", "updated_at"),
])
def test_old_run_and_every_saved_row_must_exist_by_the_decision(record, column):
    run, predictions, features = _legacy()
    if record == "run":
        run[column] = DECISION + pd.Timedelta(microseconds=1)
    else:
        table = predictions if record == "predictions" else features
        table.loc[0, column] = DECISION + pd.Timedelta(microseconds=1)
    assert _legacy_flags(run, predictions, features)


@pytest.mark.parametrize("defect", ["missing_prediction", "missing_feature", "missing_weight_key",
                                   "weight_known", "full_info", "wrong_bundle", "bad_probability"])
def test_legacy_cannot_complete_a_field_or_preweight_regime_by_guessing(defect):
    run, predictions, features = _legacy()
    if defect == "missing_prediction":
        predictions = predictions.iloc[:-1]
    elif defect == "missing_feature":
        features = features.iloc[:-1]
    elif defect == "missing_weight_key":
        features.loc[0, "features_json"] = json.dumps(dict(weight=None, weight_diff=None))
    elif defect == "weight_known":
        features.loc[0, "features_json"] = json.dumps(dict(weight=480, weight_diff=0, carried_weight_ratio=.12))
    elif defect == "full_info":
        run["logic_version"] = run["logic_version"].replace("wregime=serving", "wregime=full_info")
    elif defect == "wrong_bundle":
        run["logic_version"] = run["logic_version"].replace(BUNDLE_SHA[:12], "c" * 12)
    else:
        predictions.loc[0, "win_prob"] = "0.1"
    assert _legacy_flags(run, predictions, features)


def test_old_model_training_must_end_before_the_race():
    race = _race() | dict(race_date="2026-08-23")
    assert _legacy_flags(race=race)


def test_current_final_price_and_current_race_labels_do_not_influence_eligibility():
    entries = _entries()
    before = (_mev_flags(entries=entries), _legacy_flags(entries=entries))
    entries["odds"] = [999.9, 1.0, .1]
    entries["popularity"] = [2, 3, 1]
    entries["finish_order"] = [3, 1, 2]
    entries["won"] = [False, True, False]
    assert (_mev_flags(entries=entries), _legacy_flags(entries=entries)) == before


def test_official_settlement_uses_the_dividend_even_when_it_differs_from_judgment_price():
    frame = inputs.settlement_rows(_entries(), _results(), _payouts(), _payouts())
    winner = frame.set_index("horse_number").loc[1]
    assert bool(winner.won) and winner.official_payout_yen == 850
    assert frame.loc[~frame.won, "official_payout_yen"].eq(0).all()


@pytest.mark.parametrize("defect", ["missing_result", "extra_result", "missing_payout",
                                   "wrong_payout_number", "html_payout_disagrees", "duplicate_number"])
def test_missing_or_inconsistent_settlement_cannot_be_valued_as_losing(defect):
    entries, results, payouts, archive = _entries(), _results(), _payouts(), _payouts()
    if defect == "missing_result":
        results = results.iloc[:-1]
    elif defect == "extra_result":
        results.loc[len(results)] = dict(race_id=RID, horse_id="other", finish_order=2, result_status="finished")
    elif defect == "missing_payout":
        payouts = payouts.iloc[:0]
    elif defect == "wrong_payout_number":
        payouts.loc[0, "horse_number"] = 2
        archive.loc[0, "horse_number"] = 2
    elif defect == "html_payout_disagrees":
        archive.loc[0, "payout_yen"] = 860
    else:
        entries.loc[2, "horse_number"] = 2
    with pytest.raises(ValueError):
        inputs.settlement_rows(entries, results, payouts, archive)


def test_dead_heat_is_settled_using_each_individual_official_dividend():
    results = _results()
    results.loc[1, "finish_order"] = 1
    payouts = pd.DataFrame([dict(race_id=RID, horse_number=1, payout_yen=1340),
                            dict(race_id=RID, horse_number=2, payout_yen=1960)])
    frame = inputs.settlement_rows(_entries(), results, payouts, payouts)
    assert frame.set_index("horse_number").official_payout_yen.to_dict() == {1: 1340, 2: 1960, 3: 0}
    assert frame.dead_heat.all()
    with pytest.raises(ValueError):
        inputs.settlement_rows(_entries(), results, payouts.iloc[:1], payouts.iloc[:1])


def test_100_yen_money_back_is_a_valid_official_win():
    payouts = _payouts()
    payouts["payout_yen"] = 100
    frame = inputs.settlement_rows(_entries(), _results(), payouts, payouts)
    assert frame.loc[frame.won, "official_payout_yen"].tolist() == [100]


def test_refund_or_result_absence_does_not_silently_change_a_started_field():
    entries = _entries()
    entries.loc[2, "entry_status"] = "cancelled"
    with pytest.raises(ValueError):
        inputs.settlement_rows(entries, _results(), _payouts(), _payouts())


def test_policy_can_select_from_decision_columns_without_any_label_or_payout():
    decision = _mev().rename(columns={"odds_used": "judgment_odds"})
    decision["odds"] = decision.judgment_odds.astype(float)
    p = np.array([.55, .30, .15])
    before, ev = evaluation.select_tickets(decision, p)
    assert before.tolist() == [True, True, True]
    assert np.allclose(ev, [1.1, 1.5, 1.8])
    changed = decision.copy()
    changed["final_odds"] = [1.0, 999.9, 999.9]
    changed["won"] = [False, True, False]
    changed["official_payout_yen"] = [0, 99990, 0]
    after, after_ev = evaluation.select_tickets(changed, p)
    assert np.array_equal(before, after) and np.array_equal(ev, after_ev)


def test_pure_validators_do_not_mutate_stored_facts():
    group, entries, results, payouts = _mev(), _entries(), _results(), _payouts()
    before = deepcopy((group, entries, results, payouts))
    assert not _mev_flags(group, entries)
    inputs.settlement_rows(entries, results, payouts, payouts)
    for actual, original in zip((group, entries, results, payouts), before, strict=True):
        pd.testing.assert_frame_equal(actual, original)


def _decision_inputs():
    frame = _entries().copy()
    frame["race_date"] = "2026-10-18"
    frame["post_time"] = POST
    frame["decision_at"] = DECISION
    frame["judgment_odds"] = [3., 5., 12.]
    inverse = 1. / frame.judgment_odds
    frame["q"] = inverse / inverse.sum()
    frame["p_mev"] = [.70, .20, .10]
    frame["p_pred_preweight"] = [.65, .25, .10]
    coefficients = {
        "roi_only": dict(coefficients=dict(q=.2, p_mev=.8), fit_through="2025-12-28"),
        "plus_prediction": dict(coefficients=dict(q=.15, p_mev=.8, p_pred_preweight=.05),
                                fit_through="2025-12-28"),
    }
    return frame, coefficients


def test_runner_whitelist_makes_closing_prices_labels_and_dividends_irrelevant_to_decisions():
    frame, coefficients = _decision_inputs()
    expected = runner.decide(frame, coefficients)
    frame["odds"] = [999.9, 1.0, .5]  # Must not override judgment_odds.
    frame["final_odds"] = [.1, 999.9, 999.9]
    frame["won"] = [False, True, False]
    frame["official_payout_yen"] = [0, 99990, 0]
    actual = runner.decide(frame, coefficients)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.odds.equals(actual.judgment_odds)
    assert not {"won", "official_payout_yen", "final_odds"} & set(actual.columns)


def test_runner_uses_official_amount_after_the_fixed_decisions():
    frame, coefficients = _decision_inputs()
    decisions = runner.decide(frame, coefficients)
    settlement = inputs.settlement_rows(_entries(), _results(), _payouts(), _payouts())
    settlement["race_id"] = RID
    final, accounts, summaries = runner.settle_all(decisions, settlement)
    assert final.odds.iloc[0] == 3.
    for arm in evaluation.ARMS:
        assert bool(final.loc[final.horse_number.eq(1), arm + "_selected"].iloc[0])
        assert accounts[arm].payout.sum() == 850
        assert summaries[arm]["payout_yen"] == 850


def test_runner_rejects_a_late_decision_and_incomplete_settlement():
    frame, coefficients = _decision_inputs()
    bad = frame.copy()
    bad["decision_at"] = POST
    with pytest.raises(ValueError):
        runner.decide(bad, coefficients)
    decisions = runner.decide(frame, coefficients)
    settlement = inputs.settlement_rows(_entries(), _results(), _payouts(), _payouts())
    settlement["race_id"] = RID
    with pytest.raises(ValueError):
        runner.settle_all(decisions, settlement.iloc[:-1])
