"""Whole-field proof, causal features and shared official-payout accounting for research157."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "scripts/roi_explore/verified_recompare_157"


def _module(name):
    spec = importlib.util.spec_from_file_location(f"verified157_test_{name}", SOURCE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


data = _module("data")
evaluation = _module("evaluation")
legacy = _module("legacy")


def proof():
    entries = pd.DataFrame([
        dict(horse_id=f"h{n}", horse_number=n, entry_status="started") for n in (1, 2, 3)])
    results = pd.DataFrame([
        dict(horse_id=f"h{n}", result_status="finished", finish_order=n) for n in (1, 2, 3)])
    html = pd.DataFrame([
        dict(horse_id=f"h{n}", horse_number=n, is_started=True, row_parse_error=None,
             final_odds_text=odds, popularity=n, result_status="finished", finish_order=n)
        for n, odds in [(1, "2.5"), (2, "4.0"), (3, "8.0")]])
    payouts = pd.DataFrame([dict(horse_number=1, payout_yen=250)])
    return entries, results, html, payouts


def test_verified_race_keeps_complete_field_and_official_loser_zeros():
    entries, results, html, payouts = proof()
    before = [frame.copy(deep=True) for frame in (entries, results, html, payouts)]
    frame = data.validate_race(entries, results, html, payouts, {})
    assert frame.horse_id.tolist() == ["h1", "h2", "h3"]
    assert frame.official_payout_yen.tolist() == [250, 0, 0]
    assert frame.identity_method.eq("exact").all()
    assert frame.won.tolist() == [True, False, False]
    for original, previous in zip((entries, results, html, payouts), before, strict=True):
        pd.testing.assert_frame_equal(original, previous)


@pytest.mark.parametrize("identifier", ["nk:h1", "unrelated-horse"])
def test_same_number_or_nk_prefix_does_not_prove_identity(identifier):
    entries, results, html, payouts = proof()
    entries.loc[0, "horse_id"] = identifier
    results.loc[0, "horse_id"] = identifier
    with pytest.raises(ValueError, match="Unproven horse identity"):
        data.validate_race(entries, results, html, payouts, {})


def test_explicit_mapping_proves_id_without_changing_the_saved_ids():
    entries, results, html, payouts = proof()
    entries.loc[0, "horse_id"] = "canonical-one"
    results.loc[0, "horse_id"] = "canonical-one"
    frame = data.validate_race(entries, results, html, payouts, {"h1": "canonical-one"})
    assert frame.loc[0, ["horse_id", "html_horse_id", "identity_method"]].tolist() == [
        "canonical-one", "h1", "explicit_mapped"]


@pytest.mark.parametrize("defect", [
    "missing_result", "extra_result", "duplicate_result", "missing_printed_starter",
    "duplicate_number", "duplicate_id", "unknown_status", "missing_rank",
    "rank_disagrees", "parse_error", "missing_payout", "wrong_paid_horse",
    "duplicate_payout", "too_low_payout", "ordinary_price_mismatch",
])
def test_proof_rejects_partial_fields_and_missing_or_inconsistent_payouts(defect):
    entries, results, html, payouts = proof()
    if defect == "missing_result":
        results = results.iloc[:2]
    elif defect == "extra_result":
        results = pd.concat([results, results.iloc[[2]].assign(horse_id="extra")], ignore_index=True)
    elif defect == "duplicate_result":
        results = pd.concat([results, results.iloc[[2]]], ignore_index=True)
    elif defect == "missing_printed_starter":
        html = html.iloc[:2]
    elif defect == "duplicate_number":
        entries.loc[2, "horse_number"] = 1
    elif defect == "duplicate_id":
        html.loc[2, "horse_id"] = "h1"
    elif defect == "unknown_status":
        results.loc[2, "result_status"] = "unknown"
    elif defect == "missing_rank":
        results.loc[2, "finish_order"] = np.nan
    elif defect == "rank_disagrees":
        html.loc[2, "finish_order"] = 2
    elif defect == "parse_error":
        html.loc[2, "row_parse_error"] = "unknown status cell"
    elif defect == "missing_payout":
        payouts = payouts.iloc[:0]
    elif defect == "wrong_paid_horse":
        payouts.loc[0, "horse_number"] = 2
    elif defect == "duplicate_payout":
        payouts = pd.concat([payouts, payouts], ignore_index=True)
    elif defect == "too_low_payout":
        payouts.loc[0, "payout_yen"] = 70
    else:
        html.loc[0, "final_odds_text"] = "2.6"
    with pytest.raises(ValueError):
        data.validate_race(entries, results, html, payouts, {})


@pytest.mark.parametrize("status", ["stopped", "disqualified"])
def test_stopped_or_disqualified_counts_as_a_result_not_a_missing_starter(status):
    entries, results, html, payouts = proof()
    results.loc[2, ["result_status", "finish_order"]] = [status, np.nan]
    html.loc[2, ["result_status", "finish_order"]] = [status, np.nan]
    frame = data.validate_race(entries, results, html, payouts, {})
    assert len(frame) == 3 and frame.loc[2, "official_payout_yen"] == 0


def test_dead_heat_needs_all_winner_dividends_and_uses_the_individual_official_values():
    entries, results, html, payouts = proof()
    results.loc[1, "finish_order"] = html.loc[1, "finish_order"] = 1
    html.loc[0, "final_odds_text"] = "26.5"
    html.loc[1, "final_odds_text"] = "39.6"
    payouts.loc[0, "payout_yen"] = 1340
    with pytest.raises(ValueError, match="Payout/winner set differs"):
        data.validate_race(entries, results, html, payouts, {})
    payouts = pd.concat([payouts, pd.DataFrame([dict(horse_number=2, payout_yen=1960)])])
    frame = data.validate_race(entries, results, html, payouts, {})
    assert frame.dead_heat.all()
    assert frame.official_payout_yen.tolist() == [1340, 1960, 0]
    frame = frame.assign(race_id="r", race_date="2026-08-15", post_time="2026-08-15T03:00Z")
    accounted = evaluation.account(frame, [True, True, False])
    assert accounted.payout.sum() == 3300 and accounted.profit.sum() == 3100


def test_100_yen_official_payout_is_a_valid_hit():
    entries, results, html, payouts = proof()
    html.loc[0, "final_odds_text"] = "1.0"
    payouts.loc[0, "payout_yen"] = 100
    frame = data.validate_race(entries, results, html, payouts, {})
    assert frame.loc[0, "won"] and frame.loc[0, "official_payout_yen"] == 100


def raw_history():
    rows = []
    for day in range(1, 5):
        date = f"2026-08-{day+10:02d}"
        rid = f"202605010{day}01"
        for number in (1, 2, 3):
            rows.append(dict(race_id=rid, horse_id=f"h{number}", horse_number=number,
                frame=number, sex="牡", age=3, weight=450, weight_diff=2,
                jockey_weight=56, odds=[2.5, 4., 8.][number-1], popularity=number,
                running_style="先行", jockey_id=f"j{number}", trainer_id=f"t{number}",
                horse_row_updated_at=pd.Timestamp(date, tz="UTC"), odds_observed_fallback=False,
                race_date=date, venue_code="05", race_number=1, distance=1600,
                track_type="芝", going="良", weather="晴", race_class="未勝利", grade=None,
                prize_money=500, finish_order=number, result_status="finished",
                margin_sec=number-1, last_3f=34.+number, sire_line="その他のエクリプス系",
                damsire_line="その他のエクリプス系"))
    return pd.DataFrame(rows)


def verified_prices(raw, rid=None):
    rid = rid or raw.race_id.iloc[-1]
    d = raw[raw.race_id.eq(rid)][["race_id", "horse_id", "horse_number", "track_type"]].copy()
    d["odds"] = [3., 4., 6.]
    d["final_odds_decimal"] = ["3.0", "4.0", "6.0"]
    d["popularity"] = [1, 2, 3]
    return d


def test_verified_price_replacement_is_whole_field_and_keeps_original_immutable():
    raw = raw_history()
    previous = raw.copy(deep=True)
    fixed = verified_prices(raw)
    corrected, changes = data.replace_verified_prices(raw, fixed)
    pd.testing.assert_frame_equal(raw, previous)
    assert changes == 2
    np.testing.assert_array_equal(corrected.loc[corrected.race_id.eq(fixed.race_id.iloc[0]), "odds"], [3, 4, 6])
    pd.testing.assert_frame_equal(corrected.iloc[:-3], raw.iloc[:-3])


@pytest.mark.parametrize("defect", ["missing_id", "different_id", "wrong_number", "duplicate_raw"])
def test_verified_price_replacement_rejects_unproven_or_duplicate_raw_fields(defect):
    raw = raw_history()
    fixed = verified_prices(raw)
    target = raw.race_id.eq(fixed.race_id.iloc[0])
    if defect == "missing_id":
        raw = raw.drop(raw[target].index[-1])
    elif defect == "different_id":
        raw.loc[raw[target].index[-1], "horse_id"] = "unproved"
    elif defect == "wrong_number":
        raw.loc[raw[target].index[-1], "horse_number"] = 99
    else:
        raw = pd.concat([raw, raw[target].iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError):
        data.replace_verified_prices(raw, fixed)


def test_rebuilt_current_and_lag_market_features_use_verified_prices():
    raw = raw_history()
    fixed = verified_prices(raw, raw.race_id.iloc[-4])
    corrected, _ = data.replace_verified_prices(raw, fixed)
    rebuilt = data.market_ev.build_features(corrected)
    prior_race = fixed.race_id.iloc[0]
    prior = rebuilt[rebuilt.race_id.eq(prior_race)].set_index("horse_id")
    expected = np.array([1/3, 1/4, 1/6])
    expected /= expected.sum()
    np.testing.assert_allclose(prior.loc[["h1", "h2", "h3"], "q"], expected)
    following = rebuilt[rebuilt.race_id.eq(raw.race_id.iloc[-1])].set_index("horse_id")
    np.testing.assert_allclose(following.loc[["h1", "h2", "h3"], "prev_odds"], [3, 4, 6])
    np.testing.assert_allclose(following.loc[["h1", "h2", "h3"], "prev_q"], expected)


def test_own_target_labels_and_later_labels_do_not_change_consumed_features():
    raw = raw_history()
    target_id = raw.race_id.iloc[-4]
    changed = raw.copy()
    target_or_future = changed.race_date.ge(raw.loc[raw.race_id.eq(target_id), "race_date"].iloc[0])
    changed.loc[target_or_future, "finish_order"] = np.roll(
        changed.loc[target_or_future, "finish_order"].to_numpy().reshape(-1, 3), 1, axis=1).ravel()
    changed.loc[target_or_future, "last_3f"] = 99.
    changed.loc[target_or_future, "margin_sec"] = 50.
    left, right = data.market_ev.build_features(raw), data.market_ev.build_features(changed)
    spec = json.loads((data.MEV / "ensemble.spec.json").read_text())
    columns = spec["features"] + spec["cats"]
    pd.testing.assert_frame_equal(
        left.loc[left.race_id.eq(target_id), columns].reset_index(drop=True),
        right.loc[right.race_id.eq(target_id), columns].reset_index(drop=True))


def policy_frame():
    rows = []
    for race, day, odds in [("r1", "2026-08-15", [2., 8., 5.]), ("r2", "2026-08-16", [2., 3., 3.])]:
        for n in range(1, 4):
            rows.append(dict(race_id=race, horse_id=f"{race}-h{n}", horse_number=n,
                race_date=day, post_time=f"{day}T03:00:00Z", odds=odds[n-1],
                q=[.5, .2, .3][n-1], p_mev=[.45, .25, .3][n-1],
                p_pred_preweight=[.4, .3, .3][n-1], won=n == 1,
                official_payout_yen=200 if n == 1 else 0))
    return pd.DataFrame(rows)


def coefficients():
    return {"roi_only": dict(fit_through="2025-12-31", coefficients={"q": .4, "p_mev": .6}),
            "plus_prediction": dict(fit_through="2025-12-31",
                coefficients={"q": .4, "p_mev": .6, "p_pred_preweight": 0.})}


def test_blend_is_normalized_nested_and_independent_of_target_labels_or_row_order():
    frame, coefs = policy_frame(), coefficients()
    before = evaluation.probabilities(frame, coefs)
    np.testing.assert_allclose(before["roi_only"], before["plus_prediction"], atol=1e-15)
    for p in before.values():
        np.testing.assert_allclose(pd.Series(p).groupby(frame.race_id).sum(), 1., atol=1e-12)
    changed = frame.assign(won=~frame.won, official_payout_yen=99999)
    after = evaluation.probabilities(changed, coefs)
    for arm in before:
        np.testing.assert_array_equal(before[arm], after[arm])
    permuted = frame.sample(frac=1, random_state=157)
    result = evaluation.probabilities(permuted, coefs)
    np.testing.assert_allclose(result["roi_only"], pd.Series(before["roi_only"], index=frame.index).loc[permuted.index])


@pytest.mark.parametrize("defect", ["future", "same_day", "zero_probability", "nan_probability"])
def test_blend_rejects_future_coefficient_fit_or_invalid_components(defect):
    frame, coefs = policy_frame(), coefficients()
    if defect in ("future", "same_day"):
        coefs["roi_only"]["fit_through"] = "2026-09-01" if defect == "future" else "2026-08-15"
    else:
        frame.loc[0, "p_mev"] = 0 if defect == "zero_probability" else np.nan
    with pytest.raises(ValueError):
        evaluation.probabilities(frame, coefs)


def test_policy_is_strictly_above_one_and_does_not_read_outcomes():
    frame = policy_frame()
    p = np.tile([.5, .2, .3], 2)
    selected, ev = evaluation.select_tickets(frame, p)
    assert selected.tolist() == [False, True, True, False, False, False]
    assert ev[0] == 1.
    altered = frame.assign(won=~frame.won, official_payout_yen=np.nan)
    np.testing.assert_array_equal(evaluation.select_tickets(altered, p)[0], selected)


def test_policy_has_ten_ticket_cap_with_stable_number_then_id_tie_break():
    frame = pd.DataFrame([dict(race_id="r", horse_id=f"h{n:02d}", horse_number=n, odds=40.)
                          for n in range(12, 0, -1)])
    selected, _ = evaluation.select_tickets(frame, np.full(12, 1/12))
    assert sorted(frame.loc[selected, "horse_number"]) == list(range(1, 11))


@pytest.mark.parametrize("defect", ["partial_probability", "invalid_odds", "duplicate_id"])
def test_policy_rejects_incomplete_fields_and_invalid_prices(defect):
    frame = policy_frame()
    p = np.tile([.5, .2, .3], 2)
    if defect == "partial_probability":
        p[0] = .4
    elif defect == "invalid_odds":
        frame.loc[0, "odds"] = np.nan
    else:
        frame.loc[1, "horse_id"] = frame.loc[0, "horse_id"]
    with pytest.raises(ValueError):
        evaluation.select_tickets(frame, p)


def test_account_uses_official_amounts_and_keeps_all_zero_stake_races():
    frame = policy_frame()
    frame.loc[0, "odds"] = 999.9  # payment must not use this mutable/source price
    frame.loc[0, "official_payout_yen"] = 1340
    races = evaluation.account(frame, [True, False, False, False, False, False])
    assert races.race_id.tolist() == ["r1", "r2"]
    assert races.stake.tolist() == [100, 0]
    assert races.payout.tolist() == [1340, 0]
    assert races.profit.sum() == 1240 and races.budget.sum() == 2000
    all_cash = evaluation.account(frame, np.zeros(len(frame), dtype=bool))
    assert len(all_cash) == 2 and all_cash.stake.sum() == all_cash.payout.sum() == 0


@pytest.mark.parametrize("defect", ["missing_payout", "loser_paid", "winner_unpaid"])
def test_account_rejects_bad_official_data_even_when_the_horse_is_not_selected(defect):
    frame = policy_frame()
    if defect == "missing_payout":
        frame.loc[0, "official_payout_yen"] = np.nan
    elif defect == "loser_paid":
        frame.loc[1, "official_payout_yen"] = 100
    else:
        frame.loc[0, "official_payout_yen"] = 0
    with pytest.raises(ValueError):
        evaluation.account(frame, np.zeros(len(frame), dtype=bool))


def test_paired_bootstrap_recomputes_ratios_by_common_day_and_keeps_zero_stake_draws():
    frame = policy_frame()
    a = evaluation.account(frame, [True, False, False, True, False, False])
    b = evaluation.account(frame, [True, True, False, True, True, False])
    result = evaluation.paired(a, b, repetitions=400, seed=157)
    assert result["roi_difference"] == -1.
    assert result["profit_difference_yen"] == -200
    np.testing.assert_allclose(result["profit_common_budget_interval"], [-.1, -.1])
    np.testing.assert_allclose(result["roi_difference_interval"], [-1., -1.])
    # Some days place no bets: draws consisting only of those days have an undefined ROI.
    c = evaluation.account(frame, [True, False, False, False, False, False])
    undefined = evaluation.paired(c, c, repetitions=400, seed=157)
    assert 0 < undefined["undefined_roi_replicates"] < 400
    assert undefined["roi_difference_interval"] is None
    assert undefined["profit_common_budget_interval"] == [0., 0.]
    no_cash = evaluation.account(frame, np.zeros(len(frame), dtype=bool))
    both_cash = evaluation.paired(no_cash, no_cash, repetitions=40, seed=157)
    assert both_cash["roi_difference"] is None and both_cash["undefined_roi_replicates"] == 40


def test_paired_rejects_an_unpaired_race_population():
    frame = policy_frame()
    a = evaluation.account(frame, np.ones(len(frame), dtype=bool))
    with pytest.raises(ValueError, match="Unpaired comparison"):
        evaluation.paired(a, a.iloc[:1], repetitions=10)


def test_legacy_validation_rejects_invented_ids_bad_probability_and_future_training():
    array = np.array([[.6, .8, .9], [.4, .6, .8]])
    ids, probabilities = legacy._validated_win(["h1", "h2"], array)
    assert ids == ["h1", "h2"] and probabilities.sum() == 1.
    with pytest.raises(ValueError, match="Invalid legacy field"):
        legacy._validated_win(["h1", "h1"], array)
    with pytest.raises(ValueError, match="Invalid legacy probability"):
        legacy._validated_win(["h1", "h2"], array * 2)
    bundle = dict(train_through="2026-08-23", coefficient_train_through="2025-12-31")
    with pytest.raises(ValueError, match="trained through evaluation"):
        legacy._race_rows("r", "2026-08-23", ids, array, None, source="s",
            path=ROOT / "p", captured="2026-08-23T01:00Z", predicted="2026-08-23T01:01Z",
            post="2026-08-23T02:00Z", bundle=bundle, time_is_proxy=False)
