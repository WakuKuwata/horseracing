"""Feature 054: GET /diagnostics/segment-edge — latest-run transcription + typed 404."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.models import DiagnosticRun

pytestmark = pytest.mark.integration

_ROW = {"axis": "q_band", "segment": "q>=0.30(本命)", "n": 1000, "win_rate": 0.41,
        "logloss_p": 0.65, "logloss_q": 0.42, "gap": 0.23, "mean_p": 0.185, "mean_q": 0.405}


def _persist(session, *, computed_at, n_horses):
    session.add(DiagnosticRun(
        kind="segment_edge", logic_version="diag=segment_edge;test",
        date_from=datetime.date(2021, 1, 1), date_to=datetime.date(2025, 10, 26),
        payload={"n_horses": n_horses, "note": "SECONDARY diagnostic (047).", "rows": [_ROW]},
        computed_at=computed_at,
    ))
    session.commit()


def test_returns_latest_run_transcribed(client, session):
    _persist(session, computed_at=datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC), n_horses=1)
    _persist(session, computed_at=datetime.datetime(2026, 7, 3, tzinfo=datetime.UTC), n_horses=2)
    body = client.get("/api/v1/diagnostics/segment-edge").json()
    assert body["n_horses"] == 2                        # newest run wins
    assert body["date_from"] == "2021-01-01"
    assert "SECONDARY" in body["note"]
    assert body["rows"][0]["gap"] == 0.23 and body["rows"][0]["segment"] == "q>=0.30(本命)"


def test_typed_404_when_nothing_persisted(client, session):
    r = client.get("/api/v1/diagnostics/segment-edge")
    assert r.status_code == 404 and r.json()["code"] == "diagnostic_unavailable"


# --- Feature 083: GET /diagnostics/segment-accuracy (typed v1 transcription) ---------------------

def _sa_ci():
    return {"point": -0.5, "ci_low": -0.55, "ci_high": -0.45, "n_days": 100,
            "no_decision": False,
            "ci_note": "pointwise 95% CI, NOT adjusted for multiple comparisons"}


def _sa_bins():
    return [{"lo": 0.0, "hi": 0.1, "n": 10, "pred_mean": 0.05, "realized": 0.06,
             "wilson_low": 0.01, "wilson_high": 0.2}]


def _sa_payload():
    return {
        "instrument_contract": {
            "kind": "segment_accuracy", "secondary": True, "can_adopt": False,
            "estimand": "active-recipe historical OOF accuracy",
            "discovery_rule": "new pre-registration with discovery_run_id required",
            "ci_note": "all CIs are pointwise and NOT adjusted for multiple comparisons",
            "known_confounds": ["model-age-within-year"],
            "metric_contract_version": "sa-v1",
            "mask_library_version": "sa-mask-v1", "mask_library_hash": "mlh",
        },
        "provenance": {
            "base_model_version": "lgbm-064-f02acc", "feature_version": "features-018",
            "feature_hash": "fh", "attestation_digest": "ad", "bundle_digest": "bd",
            "prediction_checksum": "pc", "oof_race_set_hash": "orsh",
            "scored_race_set_hash": "srsh", "label_snapshot_hash": "lsh",
            "train_floor": "full-history", "eval_window": ["2019-01-01", "2026-07-12"],
            "first_valid_year": 2019, "fold_boundaries": [2019, 2020],
            "probability_stage": "model-internal calibrated win prob", "code_sha": "sha",
            "seed": 20260725, "bootstrap_b": 2000,
            "metric_contract_version": "sa-v1",
            "mask_library_version": "sa-mask-v1", "mask_library_hash": "mlh",
        },
        "population": {"n_scored_races": 2, "n_scored_horses": 20,
                       "exclusions": {"ineligible_winner_count": 1}},
        "axes": [
            {"axis_id": "surface", "family": "race_core", "grain": "race", "origin": "core",
             "definition": {"buckets": ["芝", "ダ", "障"]}, "mask_definition_hash": "h1",
             "buckets": {"芝": {
                 "grain": {"winner_nll": "race",
                           "calibration": "started_horse_within_selected_races"},
                 "n_races": 2, "n_horses": 20, "excess_nll_uniform": _sa_ci(),
                 "winner_nll": 2.0, "uniform_nll": 2.5,
                 "market": {"n_market_complete_races": 2, "n_total_races": 2,
                            "market_nll": 1.9, "winner_nll_market_subset": 2.0,
                            "excess_nll_market": 0.1},
                 "by_year": {"2024": {"n_races": 2, "excess_nll_uniform_point": -0.5}},
                 "calibration": {"grain_note": "note", "bins": _sa_bins(), "ece": 0.001,
                                 "calibration_in_the_large": None,
                                 "citl_note": "structurally 0 at race grain", "n": 20},
                 "ece_ci": _sa_ci()}}},
            {"axis_id": "rotation_band", "family": "post_081_exploratory", "grain": "horse",
             "origin": "post_081_exploratory", "definition": {"edges_days": [8, 15, 29, 71]},
             "mask_definition_hash": "h2",
             "buckets": {">70": {
                 "grain": {"excess_logloss": "horse",
                           "winner_nll": "NOT_AVAILABLE_AT_HORSE_GRAIN"},
                 "n_horses": 10, "n_races": 2, "excess_logloss_vs_uniform": _sa_ci(),
                 "by_year": {"2024": {"n_horses": 10, "excess_logloss_point": -0.03}},
                 "calibration": {"grain_note": "note", "bins": _sa_bins(), "ece": 0.002,
                                 "calibration_in_the_large": -0.0068, "n": 10},
                 "ece_ci": _sa_ci()}}},
        ],
    }


def _persist_sa(session, *, computed_at, payload):
    session.add(DiagnosticRun(
        kind="segment_accuracy", logic_version="segment-accuracy;model=lgbm-064-f02acc",
        date_from=datetime.date(2019, 1, 1), date_to=datetime.date(2026, 7, 12),
        payload=payload, computed_at=computed_at,
    ))
    session.commit()


def test_segment_accuracy_full_roundtrip_semantic_equality(client, session):
    """persist -> JSONB -> typed transcription: canonical deep equality (JSONB does not keep
    key order, so semantic — not byte — equality is the contract)."""
    payload = _sa_payload()
    _persist_sa(session, computed_at=datetime.datetime(2026, 7, 25, tzinfo=datetime.UTC),
                payload=payload)
    body = client.get("/api/v1/diagnostics/segment-accuracy").json()
    assert body["kind"] == "segment_accuracy"
    assert body["diagnostic_run_id"]                     # discovery handle present (P1#5)
    assert body["payload"] == payload                    # dict equality = order-insensitive
    ic = body["payload"]["instrument_contract"]
    assert ic["secondary"] is True and ic["can_adopt"] is False
    # race-grain citl is null + identity note (never a "0 = well calibrated" read)
    cal = body["payload"]["axes"][0]["buckets"]["芝"]["calibration"]
    assert cal["calibration_in_the_large"] is None and "structurally 0" in cal["citl_note"]


def test_segment_accuracy_unknown_version_fails_closed(client, session):
    payload = _sa_payload()
    payload["instrument_contract"]["metric_contract_version"] = "sa-v99"
    _persist_sa(session, computed_at=datetime.datetime(2026, 7, 25, tzinfo=datetime.UTC),
                payload=payload)
    r = client.get("/api/v1/diagnostics/segment-accuracy")
    assert r.status_code == 409 and r.json()["code"] == "diagnostic_contract_unsupported"


def test_segment_accuracy_extra_key_fails_closed(client, session):
    """codex P0#1: a key typo / foreign field must NOT render as silent nulls (075 trap)."""
    payload = _sa_payload()
    payload["axes"][0]["buckets"]["芝"]["worst_rank"] = 1     # foreign key sneaks in
    _persist_sa(session, computed_at=datetime.datetime(2026, 7, 25, tzinfo=datetime.UTC),
                payload=payload)
    r = client.get("/api/v1/diagnostics/segment-accuracy")
    assert r.status_code == 409 and r.json()["code"] == "diagnostic_contract_unsupported"


def test_segment_accuracy_missing_key_fails_closed(client, session):
    payload = _sa_payload()
    del payload["provenance"]["bundle_digest"]
    _persist_sa(session, computed_at=datetime.datetime(2026, 7, 25, tzinfo=datetime.UTC),
                payload=payload)
    r = client.get("/api/v1/diagnostics/segment-accuracy")
    assert r.status_code == 409


def test_segment_accuracy_empty_payload_fails_closed(client, session):
    _persist_sa(session, computed_at=datetime.datetime(2026, 7, 25, tzinfo=datetime.UTC),
                payload={})
    r = client.get("/api/v1/diagnostics/segment-accuracy")
    assert r.status_code == 409


def test_segment_accuracy_typed_404_when_nothing_persisted(client, session):
    r = client.get("/api/v1/diagnostics/segment-accuracy")
    assert r.status_code == 404 and r.json()["code"] == "diagnostic_unavailable"
