"""Prospective budget reservations cannot silently reset, duplicate, or backdate."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "small_gain_budget_test_driver", Path(__file__).resolve().parents[1] / "small_gain_budget.py")
driver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(driver)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(driver, "_now", lambda: dt.datetime(2026, 9, 7, 12, tzinfo=dt.timezone.utc))


@pytest.fixture
def policy():
    return {"version": "small-gains-v1", "confirmation": {
        "max_submissions_per_year": 8, "annual_one_sided_alpha": .05,
        "bootstrap_alpha": .0125, "bootstrap_b": 4000, "required_seed_count": 3},
        "gate_template": {"evaluation_contract_version": "v4", "primary_metric": "winner_nll",
                          "min_effect_delta": 0,
                          "delta_derivation_ref": "specs/112-small-gain-adoption/delta-derivation.json",
                          "bootstrap": {"block": "race_day", "b": 2000, "alpha": .05},
                          "arms": {"seed": 42},
                          "seed_noise": {"sd_fold": .001816, "k_seeds": 1}}}


@pytest.fixture
def manifest():
    return {"bundle_id": "bundle-1", "bundle_manifest_sha256": "a" * 64,
            "anchor_model_sha256": "b" * 64, "selection_used_through": "2026-09-06",
            "frozen_at": "2026-09-07T00:00:00Z",
            "eval_window": {"from": "2026-09-08", "to": "2027-09-07", "min_eval_days": 100},
            "seed_check": {"seeds": [42, 43, 44], "mean_diff": -.0002,
                           "selected_seed": 42, "fixed_before_results": True},
            "primary_regime": "full_information", "power_plan_ref": "power-plan.json"}


def test_success_records_first_slot_and_preserves_inputs(tmp_path, manifest, policy):
    originals = copy.deepcopy((manifest, policy))
    path = tmp_path / "ledger.json"
    config = driver.prepare_confirmation(manifest, policy, path)
    ledger = json.loads(path.read_text())
    assert config["min_effect_delta"] == 0
    assert config["bootstrap"]["alpha"] == .0125
    assert config["bootstrap"]["b"] == 4000
    assert config["eval_window"] == manifest["eval_window"]
    assert config["adoption_policy"]["ledger_sha256"] == driver._hash(ledger)
    assert config["adoption_policy"]["slot"] == 1
    assert ledger["reservations"][0]["reserved_at"] == "2026-09-07T12:00:00+00:00"
    assert (manifest, policy) == originals


def test_exhaustion_is_persistent_and_next_year_has_separate_budget(tmp_path, manifest, policy):
    path = tmp_path / "ledger.json"
    for n in range(8):
        manifest["bundle_id"] = f"bundle-{n}"
        manifest["bundle_manifest_sha256"] = f"{n:064x}"
        assert driver.prepare_confirmation(manifest, policy, path)["adoption_policy"]["slot"] == n + 1
    manifest["bundle_id"] = "bundle-9"
    manifest["bundle_manifest_sha256"] = "f" * 64
    before = path.read_bytes()
    with pytest.raises(driver.BudgetError, match="exhausted"):
        driver.prepare_confirmation(manifest, policy, path)
    assert path.read_bytes() == before
    manifest["eval_window"] = {"from": "2027-01-01", "to": "2027-12-31", "min_eval_days": 100}
    assert driver.prepare_confirmation(manifest, policy, path)["adoption_policy"]["slot"] == 1


@pytest.mark.parametrize("mutation", ["same", "rename", "digest"])
def test_duplicate_cannot_be_resubmitted_or_renamed(tmp_path, manifest, policy, mutation):
    path = tmp_path / "ledger.json"
    driver.prepare_confirmation(manifest, policy, path)
    if mutation == "rename":
        manifest["bundle_id"] = "renamed"
    elif mutation == "digest":
        manifest["bundle_manifest_sha256"] = "c" * 64
    before = path.read_bytes()
    with pytest.raises(driver.BudgetError, match="already reserved"):
        driver.prepare_confirmation(manifest, policy, path)
    assert path.read_bytes() == before


def test_changed_template_cannot_reset_ledger(tmp_path, manifest, policy):
    path = tmp_path / "ledger.json"
    driver.prepare_confirmation(manifest, policy, path)
    policy["gate_template"]["seed_noise"]["sd_fold"] = .002
    with pytest.raises(driver.BudgetError, match="policy/config changed"):
        driver.prepare_confirmation(manifest, policy, path)


@pytest.mark.parametrize("key,value", [
    ("max_submissions_per_year", 9), ("annual_one_sided_alpha", .1),
    ("bootstrap_alpha", .05), ("bootstrap_b", 2000), ("required_seed_count", 2)])
def test_budget_plan_is_fixed(tmp_path, manifest, policy, key, value):
    policy["confirmation"][key] = value
    with pytest.raises(driver.BudgetError, match="plan changed"):
        driver.prepare_confirmation(manifest, policy, tmp_path / "ledger.json")


@pytest.mark.parametrize("change", [
    {"selection_used_through": "2026-09-08"},
    {"frozen_at": "2026-09-08T00:00:00Z"},
    {"frozen_at": "2026-09-07T00:00:00"},
    {"frozen_at": "2026-09-07T00:00:00+09:00"},
    {"eval_window": {"from": "2026-09-07", "to": "2027-09-07", "min_eval_days": 100}},
    {"frozen_at": "2026-09-05T00:00:00Z", "selection_used_through": "2026-09-04",
     "eval_window": {"from": "2026-09-06", "to": "2027-09-07", "min_eval_days": 100}},
    {"eval_window": {"from": "2026-09-08", "to": "2026-09-07", "min_eval_days": 1}},
    {"eval_window": {"from": "2026-09-08", "to": "2027-09-07", "min_eval_days": 0}},
    {"eval_window": {"from": "2026-09-08", "to": "2026-09-09", "min_eval_days": 100}},
])
def test_temporal_failures_leave_no_ledger(tmp_path, manifest, policy, change):
    manifest.update(change)
    path = tmp_path / "ledger.json"
    with pytest.raises(driver.BudgetError):
        driver.prepare_confirmation(manifest, policy, path)
    assert not path.exists()


@pytest.mark.parametrize("key,value", [
    ("bundle_manifest_sha256", "bad"), ("anchor_model_sha256", "G" * 64),
    ("primary_regime", ""), ("power_plan_ref", " "), ("bundle_id", "")])
def test_identity_and_refs_required(tmp_path, manifest, policy, key, value):
    manifest[key] = value
    with pytest.raises(driver.BudgetError):
        driver.prepare_confirmation(manifest, policy, tmp_path / "ledger.json")


@pytest.mark.parametrize("key,value", [
    ("seeds", [42, 42, 43]), ("seeds", [42, 43]), ("seeds", [True, 42, 43]),
    ("mean_diff", float("nan")), ("mean_diff", float("inf")), ("mean_diff", 0),
    ("mean_diff", .001), ("selected_seed", 45), ("fixed_before_results", False)])
def test_seed_evidence_required(tmp_path, manifest, policy, key, value):
    manifest["seed_check"][key] = value
    with pytest.raises(driver.BudgetError):
        driver.prepare_confirmation(manifest, policy, tmp_path / "ledger.json")


def test_corrupt_ledger_fails_closed(tmp_path, manifest, policy):
    path = tmp_path / "ledger.json"
    driver.prepare_confirmation(manifest, policy, path)
    ledger = json.loads(path.read_text())
    ledger["reservations"][0]["manifest"]["anchor_model_sha256"] = "c" * 64
    path.write_text(json.dumps(ledger))
    with pytest.raises(driver.BudgetError, match="manifest changed"):
        driver.prepare_confirmation(manifest, policy, path)


def test_clock_crossing_before_reservation_does_not_consume_slot(tmp_path, manifest, policy, monkeypatch):
    dates = iter([dt.datetime(2026, 9, 7, 12, tzinfo=dt.timezone.utc),
                  dt.datetime(2026, 9, 8, 0, tzinfo=dt.timezone.utc)])
    monkeypatch.setattr(driver, "_now", lambda: next(dates))
    path = tmp_path / "ledger.json"
    with pytest.raises(driver.BudgetError, match="actual reservation date"):
        driver.prepare_confirmation(manifest, policy, path)
    assert not path.exists()


def test_selected_seed_must_match_recipe(tmp_path, manifest, policy):
    manifest["seed_check"]["selected_seed"] = 43
    with pytest.raises(driver.BudgetError, match="arms.seed"):
        driver.prepare_confirmation(manifest, policy, tmp_path / "ledger.json")


def test_three_seed_screen_does_not_reduce_single_model_noise(tmp_path, manifest, policy):
    policy["gate_template"]["seed_noise"]["k_seeds"] = 3
    with pytest.raises(driver.BudgetError, match="k_seeds=1"):
        driver.prepare_confirmation(manifest, policy, tmp_path / "ledger.json")
