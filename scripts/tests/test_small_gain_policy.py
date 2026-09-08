"""The new workflow admits supported tiny gains without relaxing the old contract."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import small_gain_policy as driver
import small_gain_budget as budget
from horseracing_eval.decision import assert_confirmatory, gate_config_hash
from horseracing_eval.delta_provenance import assert_delta_provenance
from horseracing_eval.gates import evaluate_core_gate


@pytest.fixture
def manifest():
    return {"bundle_id": "test-only", "bundle_manifest_sha256": "a" * 64,
            "anchor_model_sha256": "b" * 64, "selection_used_through": "2026-09-06",
            "frozen_at": "2026-09-07T00:00:00Z",
            "eval_window": {"from": "2026-09-08", "to": "2027-09-07", "min_eval_days": 100},
            "seed_check": {"seeds": [42, 43, 44], "mean_diff": -.0001,
                           "selected_seed": 42, "fixed_before_results": True},
            "primary_regime": "preweight", "power_plan_ref": "test-only-plan"}


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(budget, "_now", lambda: dt.datetime(2026, 9, 7, 12, tzinfo=dt.timezone.utc))
    return tmp_path


def test_prepared_config_passes_existing_contract_and_hash_checks(isolated, manifest):
    output = isolated / "config.json"
    cfg = driver.prepare(manifest, output)
    saved = json.loads(output.read_text())
    assert saved == cfg
    digest = output.with_suffix(".hash.txt").read_text().strip()
    assert_confirmatory(saved, expected_hash=digest, eval_window=manifest["eval_window"])
    assert assert_delta_provenance(saved, root=driver.ROOT)["derived_delta"] == 0
    assert cfg["adoption_policy"]["slot"] == 1
    before = driver.LEDGER.read_bytes()
    with pytest.raises(FileExistsError):
        driver.prepare(manifest, output)
    assert driver.LEDGER.read_bytes() == before


def test_invalid_provenance_does_not_consume_slot(isolated, manifest, monkeypatch):
    cfg = driver.load_policy()
    cfg["gate_template"]["delta_derivation_ref"] = "missing.json"
    monkeypatch.setattr(driver, "load_policy", lambda: cfg)
    with pytest.raises(RuntimeError):
        driver.prepare(manifest, isolated / "config.json")
    assert not driver.LEDGER.exists()


def test_invalid_manifest_does_not_consume_slot(isolated, manifest):
    manifest["eval_window"]["from"] = "2026-09-06"
    with pytest.raises(budget.BudgetError):
        driver.prepare(manifest, isolated / "config.json")
    assert not driver.LEDGER.exists()


@pytest.mark.parametrize("upper,expected", [(-.00001, True), (0.0, False), (.00001, False)])
def test_tiny_gain_requires_interval_support(upper, expected):
    cfg = driver.load_policy()["gate_template"]
    args = dict(diff=-.0001, ci_low=-.0002, ci_high=upper, recent={"pass": True},
                top2_diff=0, top3_diff=0, cand_ece=.001, act_ece=.001)
    assert evaluate_core_gate(**args, cfg=cfg).adopted is expected
    legacy = json.loads((driver.ROOT / "specs/111-ability-observation/gate-config.json").read_text())
    assert evaluate_core_gate(**args, cfg=legacy).adopted is False


def test_tiny_gain_still_fails_quality_guard():
    cfg = driver.load_policy()["gate_template"]
    result = evaluate_core_gate(diff=-.0001, ci_low=-.0002, ci_high=-.00001,
                               recent={"pass": True}, top2_diff=.0006, top3_diff=0,
                               cand_ece=.001, act_ece=.001, cfg=cfg)
    assert not result.adopted


def test_policy_hash_is_checked(tmp_path, monkeypatch):
    policy = driver.load_policy()
    (tmp_path / "policy.json").write_text(json.dumps(policy))
    (tmp_path / "policy.hash.txt").write_text("0" * 64)
    monkeypatch.setattr(driver, "POLICY_DIR", tmp_path)
    with pytest.raises(ValueError, match="hash mismatch"):
        driver.load_policy()
