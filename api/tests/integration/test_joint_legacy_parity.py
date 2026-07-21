"""Feature 076 US4 (T023): dispersion activation must NOT change the exotic joint (λ=1 invariant).

The `?bet_type=&top=K` joint is derived from win p by the 009 engine and is independent of the 066
dispersion read-out. Turning dispersion activation ON (configuring a manifest) may populate the
display-only model_delta, but the joint probabilities must be byte-identical (the API/exotic joint
stays λ=1 — allowed-change matrix, data-model §5).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest
from horseracing_probability.calib_manifest import build_manifest

from tests._synth import seed_model, seed_race

pytestmark = pytest.mark.integration

_RACE = "202506010101"
_MODEL = "lgbm-063"  # manifest base is hardcoded to lgbm-063 (074)
_AFTER = datetime.date(2025, 6, 1)  # after the manifest fit_through
_HORSES = {
    1: {"win": 0.45, "odds": 2.0}, 2: {"win": 0.25, "odds": 3.5},
    3: {"win": 0.18, "odds": 6.0}, 4: {"win": 0.12, "odds": 9.0},
}


def _manifest(tmp_path: Path) -> str:
    dig = "a" * 64
    m = build_manifest(
        attestation_digest=dig, bundle_digest="b" * 64,
        evaluation={"evaluation_contract_version": "v2", "base_model_version": _MODEL,
                    "attestation_digest": dig, "bundle_digest": "b" * 64, "verdict": "ADOPT"},
        stages=["model_win", "two_gamma_win", "stage_discount_top2", "stage_discount_top3"],
        code_sha="076cafe", seed=7, num_threads=1,
        two_gamma_lambda_full_precision={
            "two_gamma": {"gamma_lo": 1.6, "gamma_hi": 0.5, "pivot": 0.15},
            "stage_lambdas": {"top2": 0.82, "top3": 0.70}},
        fit_through="2024-12-31", artifact_scope="production", activation_eligible=True)
    p = (tmp_path / "m.json").resolve()
    p.write_text(json.dumps(m), encoding="utf-8")
    return str(p)


def _joint(client, bet_type="trifecta", top=10):
    r = client.get(f"/api/v1/races/{_RACE}/predictions", params={"bet_type": bet_type, "top": top})
    assert r.status_code == 200
    return r.json()


def test_dispersion_activation_leaves_the_joint_byte_identical(client, session, tmp_path, monkeypatch):
    seed_model(session, model_version=_MODEL)
    seed_race(session, race_id=_RACE, horses=_HORSES, race_date=_AFTER, model_version=_MODEL)

    monkeypatch.delenv("DISPERSION_CALIB_MANIFEST", raising=False)
    before = _joint(client)
    assert before["joint"] and before["race_dispersion"].get("model_delta") is None

    monkeypatch.setenv("DISPERSION_CALIB_MANIFEST", _manifest(tmp_path))
    after = _joint(client)

    # activation is genuinely ON (the display model_delta now populates)...
    assert after["race_dispersion"]["model_delta"] is not None
    # ...yet the exotic joint is byte-identical (λ=1 unaffected — the whole point)
    assert after["joint"] == before["joint"]
    assert after["joint_logic_version"] == before["joint_logic_version"]
