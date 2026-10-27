"""132 mixture contracts: frozen weights, annual transfer, and no training."""
from copy import deepcopy
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accuracy_colsample_recheck as study
from horseracing_eval.predictor import Prediction
from horseracing_training.predictor import assemble_predictions

IDS = ("a", "b", "c", "d")


def heads(win):
    return study.prediction_array(assemble_predictions(list(IDS), np.asarray(win), eps=0.), IDS)


def example():
    members = np.stack([heads(win) for win in ([.7,.1,.1,.1], [.1,.6,.2,.1], [.15,.2,.55,.1],
        [.3,.2,.1,.4], [.4,.1,.3,.2], [.2,.4,.1,.3])])
    return members, heads([.1,.1,.1,.7]), heads([.2,.2,.3,.3])


@pytest.mark.parametrize("arm", list(study.ARMS))
def test_fixed_all_head_weights_and_explicit_replacement(arm):
    members, raw, transferred = example()
    result = study.mix_arm(members, raw, transferred, arm)
    if arm == "replace_joint42":
        expected = np.stack([transferred, *members[1:]]).mean(0)
    else:
        a = study.ARMS[arm]
        weight = a["weight"][0] / a["weight"][1]
        expected = (1-weight)*members.mean(0) + weight*(transferred if a["transfer"] else raw)
    np.testing.assert_array_equal(result, expected)
    assert np.max(np.abs(result[:, 1:] - heads(result[:, 0])[:, 1:])) > 1e-4


@pytest.mark.parametrize("failure", ["missing_member", "extra_member", "nan", "sum", "order", "wrong_horse_count", "unknown_arm"])
def test_malformed_mixture_rejected(failure):
    members, raw, transferred = example()
    arm = "add_raw_1of7"
    if failure == "missing_member": members = members[:5]
    elif failure == "extra_member": members = np.concatenate([members, members[:1]])
    elif failure == "nan": raw[0, 0] = np.nan
    elif failure == "sum": raw[0, 0] += .1
    elif failure == "order": raw[0, 1] = .01
    elif failure == "wrong_horse_count": raw = raw[:3]
    else: arm = "unregistered"
    with pytest.raises(ValueError):
        study.mix_arm(members, raw, transferred, arm)


def coefficients():
    coef = {"training_seed": 42, "base_arm": "pruning125_raw", "base_recipe_hash": "old125",
        "coefficient_order": study.JOINT_TERMS, "selected": ["prior_gap", "global_temperature"],
        "gammas": {str(year): [0., 0., 0., 0., .1] for year in study.YEARS},
        "can_adopt": False, "eligible_for_verdict": False,
        "fit_diagnostics": [{"eval_year": year, "fit_last_day": f"{year-1}-12-28", "eval_first_day": f"{year}-01-05"}
                            for year in study.YEARS]}
    return coef, {"base_recipe_hashes": {"42": "old125"}}


@pytest.mark.parametrize("failure", ["future", "missing_year", "nonpositive_temperature", "wrong_source", "year_order", "nan"])
def test_transfer_checks_source_recipe_and_strict_past_year(failure):
    coef, frozen = coefficients()
    study.validate_coefficients(coef, frozen)
    if failure == "future": coef["fit_diagnostics"][0]["fit_last_day"] = "2020-01-01"
    elif failure == "missing_year": del coef["gammas"]["2020"]
    elif failure == "nonpositive_temperature": coef["gammas"]["2020"][-1] = -1.
    elif failure == "wrong_source": coef["base_recipe_hash"] = "new126"
    elif failure == "year_order": coef["fit_diagnostics"].reverse()
    else: coef["gammas"]["2020"][0] = float("nan")
    with pytest.raises(ValueError):
        study.validate_coefficients(coef, frozen)


def test_temperature_uses_candidate_probability_and_matching_annual_coefficient():
    coef, frozen = coefficients()
    coef["gammas"]["2020"][-1] = .5
    coef["gammas"]["2021"][-1] = .9
    study.validate_coefficients(coef, frozen)
    inputs = pd.DataFrame({"race_id": ["202006010101"]*4, "horse_id": IDS,
        "race_date": [dt.date(2020,1,5)]*4, "gap_log": np.zeros(4), "prior_gap_log": np.nan,
        "female_sin": np.zeros(4), "female_cos": np.zeros(4)})
    record = SimpleNamespace(race_id="202006010101", race_date=dt.date(2020,1,5), year=2020,
                             ids=IDS, correction_inputs=inputs)
    raw = heads([.1,.2,.3,.4])
    result = study.transferred_predictions(record, raw, coef)
    expected = raw[:,0] ** 1.5
    np.testing.assert_allclose(result[:,0], expected/expected.sum(), atol=1e-14, rtol=0)


@pytest.mark.parametrize("day", [dt.date(2026,9,12), dt.date(2026,9,13), None, "2020-01-01", dt.datetime(2020,1,1)])
def test_newest_data_or_unknown_date_is_rejected(day):
    with pytest.raises(ValueError): study.validate_day(day)


def test_preserve_existing_artifact_and_detect_changed_input(tmp_path):
    path = tmp_path/"original.json"
    study.write_json(path, {"original": True})
    expected = {str(path): study.digest(path)}
    with pytest.raises(FileExistsError): study.write_json(path, {"original": False})
    study._assert_unchanged(expected)
    path.write_text("changed")
    with pytest.raises(ValueError, match="Frozen input changed"):
        study._assert_unchanged(expected)


def test_training_is_explicitly_disabled():
    import lightgbm as lgb
    with study.prohibit_training():
        with pytest.raises(RuntimeError, match="training is prohibited"):
            lgb.train({}, None)
        with pytest.raises(RuntimeError, match="training is prohibited"):
            lgb.LGBMRegressor().fit([[1.]], [1.])


def test_day_bootstrap_uses_ratio_not_unweighted_mean_of_daily_means():
    rows = [(dt.date(2020,1,1), -4., 4), (dt.date(2020,1,2), 0., 1)]
    ci = study.day_ci(rows, margin=.005)
    assert ci["point"] == -.8
    assert ci["n_items"] == 5 and ci["n_days"] == 2


def test_small_gain_retained_but_quality_failure_and_missing_evidence_not_hidden():
    baseline = {"winner_nll": 2., "heads": {head: {"logloss": .2, "ece": .001} for head in ("win","top2","top3")}}
    candidate = deepcopy(baseline); candidate["winner_nll"] -= .0001
    diagnostics = {"recent": {"recent_3y": {"state": "NOT_PROVEN"}, "recent_5y": {"state": "PASS"}},
                   "critical": {name: {"state": "PASS"} for name in ("canonical", "nk", "recent_year_only")}}
    result = study.research_disposition(baseline, candidate, diagnostics)
    assert result["state"] == "RETAIN_UNCERTAIN" and result["can_adopt"] is False
    candidate["heads"]["top3"]["logloss"] += .001
    assert study.research_disposition(baseline, candidate, diagnostics)["state"] == "QUALITY_REVIEW_REQUIRED"
    candidate = deepcopy(baseline); candidate["winner_nll"] -= .0001
    diagnostics["critical"]["nk"]["state"] = "MISSING"
    assert study.research_disposition(baseline, candidate, diagnostics)["state"] == "QUALITY_REVIEW_REQUIRED"


def test_no_new_weight_search_and_no_model_training_in_protocol():
    assert len(study.ARMS) == 4
    assert study.POLICY["new_booster_fits"] == study.POLICY["new_coefficient_fits"] == 0
    assert study.POLICY["post_mixture_processing"] is None
    assert study.POLICY["regime"] == "full_information"


def test_registered_experiment_and_internal_arm_mapping():
    config = study.read_json(study.ROOT / "specs/132-accuracy-first-batch/experiment.json")
    study.validate_experiment(config)
    config["colsample"]["arms"]["raw_add"]["candidate_weight"] = "1/8"
    with pytest.raises(ValueError, match="registered colsample"):
        study.validate_experiment(config)


def test_calibration_guard_uses_three_head_mean():
    baseline = {"winner_nll": 2., "heads": {head: {"logloss": .2, "ece": .001}
                                                   for head in ("win", "top2", "top3")}}
    candidate = deepcopy(baseline)
    candidate["winner_nll"] -= .001
    candidate["heads"]["top2"]["ece"] += .002
    candidate["heads"]["top3"]["ece"] += .002
    diagnostics = {"recent": {"recent_3y": {"state": "PASS"}},
                   "critical": {"canonical": {"state": "PASS"}}}
    result = study.research_disposition(baseline, candidate, diagnostics)
    assert result["numeric_quality"]["calibration_noninferior"] is False
    assert result["state"] == "QUALITY_REVIEW_REQUIRED"
    assert result["calibration"]["mean_ece_diff"] == pytest.approx(.004/3)
