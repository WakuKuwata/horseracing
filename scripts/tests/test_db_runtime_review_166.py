"""Independent review attacks; no DB connection and no network collection are used."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil

import pandas as pd
import pytest

from scripts.roi_explore.db_runtime_166 import core
from horseracing_features.loader import Frames


def example_quote():
    field = [dict(horse_id=f"h-{n}", horse_number=n) for n in (1, 2, 3)]
    original = [
        dict(**r, odds=o, popularity=n)
        for n, (r, o) in enumerate(zip(field, (2, 4, 8), strict=True), 1)
    ]
    snapshot = dict(
        race_id=core.TARGET,
        chaos_snapshot_id=core.SNAPSHOT_ID,
        captured_at="2026-10-25T05:40:00+00:00",
        field=original,
        n=3,
        content_digest=core.c.digest(original),
    )
    return snapshot, field


def test_saved_quote_keeps_original_clock_and_labels_missing_http():
    snapshot, field = example_quote()
    quote = core.saved_quote(snapshot, field, [])
    assert (
        quote["received_at"] == quote["original_captured_at"] == snapshot["captured_at"]
    )
    assert quote["original_fetch_started_at"] is None and quote["provider_at"] is None
    assert (
        quote["full_raw_http_saved"]
        is quote["evidence_admissible"]
        is quote["eligible_for_prospective"]
        is False
    )
    assert quote["raw_sha256"] == core.c.digest(snapshot)
    assert "fetch interval not saved" in quote["timestamp_semantics"]


@pytest.mark.parametrize(
    "mutation", ["race", "id", "digest", "count", "field", "popularity", "price"]
)
def test_saved_quote_rejects_inconsistent_saved_rows(mutation):
    snapshot, field = example_quote()
    if mutation == "race":
        snapshot["race_id"] = "202601010101"
    elif mutation == "id":
        snapshot["chaos_snapshot_id"] = "other"
    elif mutation == "digest":
        snapshot["content_digest"] = "bad"
    elif mutation == "count":
        snapshot["n"] = 4
    elif mutation == "field":
        field[-1]["horse_id"] = "other"
    elif mutation == "popularity":
        snapshot["field"][0]["popularity"] = 0
    elif mutation == "price":
        snapshot["field"][0]["odds"] = 0.9
    if mutation in ("popularity", "price"):
        snapshot["content_digest"] = core.c.digest(snapshot["field"])
    with pytest.raises(ValueError):
        core.saved_quote(snapshot, field, [])


def test_saved_quote_maps_only_explicit_unique_current_ids():
    snapshot, field = example_quote()
    snapshot["field"][0]["horse_id"] = "source-a"
    snapshot["content_digest"] = core.c.digest(snapshot["field"])
    mapping = [dict(source_id="source-a", canonical_id="h-1", mapping_status="mapped")]
    quote = core.saved_quote(snapshot, field, mapping)
    assert quote["rows"][0]["horse_id"] == "h-1"
    assert quote["current_replay_identity_map"] == [
        dict(source_id="source-a", canonical_id="h-1")
    ]
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        core.saved_quote(snapshot, field, [])
    with pytest.raises(ValueError, match="missing_or_ambiguous"):
        core.saved_quote(
            snapshot,
            field,
            mapping
            + [dict(source_id="source-a", canonical_id="h-2", mapping_status="mapped")],
        )
    with pytest.raises(ValueError, match="mapping_number"):
        core.saved_quote(
            snapshot,
            field,
            [dict(source_id="source-a", canonical_id="h-2", mapping_status="mapped")],
        )


def frames_and_raw():
    races = pd.DataFrame(
        [
            dict(race_id="past", race_date="2026-10-24"),
            dict(race_id="target", race_date="2026-10-25"),
            dict(race_id="same", race_date="2026-10-25"),
        ]
    )
    results = pd.DataFrame(
        [
            dict(race_id=r, horse_id="h-" + r, finish_order=1)
            for r in ("past", "target", "same")
        ]
    )
    frames = Frames(
        races,
        pd.DataFrame([dict(race_id="target", horse_id="h-target")]),
        results,
        pd.DataFrame([dict(horse_id="h-target")]),
    )
    raw = pd.DataFrame(
        [
            dict(
                race_id=r.race_id,
                race_date=r.race_date,
                horse_id="h-" + r.race_id,
                finish_order=1,
                result_status="finished",
                last_3f=34.0,
                margin_sec=0.0,
                odds=2.0,
                popularity=1,
            )
            for r in races.itertuples()
        ]
    )
    return frames, raw


def test_mask_removes_all_same_day_results_only_in_copies():
    frames, raw = frames_and_raw()
    original_raw = raw.copy(deep=True)
    original_results = frames.race_results.copy(deep=True)
    clean, masked, evidence = core.mask_same_day(frames, raw, day="2026-10-25")
    pd.testing.assert_frame_equal(raw, original_raw)
    pd.testing.assert_frame_equal(frames.race_results, original_results)
    assert list(clean.race_results.race_id) == ["past"]
    cols = ["finish_order", "result_status", "last_3f", "margin_sec"]
    assert masked.loc[masked.race_id.isin(["target", "same"]), cols].isna().all().all()
    pd.testing.assert_frame_equal(
        masked.loc[masked.race_id.eq("past"), cols],
        raw.loc[raw.race_id.eq("past"), cols],
        check_dtype=False,
    )
    assert masked.odds.equals(raw.odds) and masked.popularity.equals(raw.popularity)
    assert evidence["removed_legacy_result_rows"] == evidence["masked_mev_rows"] == 2
    assert (
        evidence["source_objects_changed"]
        is evidence["database_changed"]
        is evidence["historical_asof_reconstruction"]
        is evidence["primary_eligible"]
        is False
    )


@pytest.mark.parametrize("pool", ["legacy", "mev"])
def test_mask_does_not_let_future_or_pre2007_pool_pass(pool):
    frames, raw = frames_and_raw()
    if pool == "legacy":
        frames.races.loc[0, "race_date"] = "2026-10-26"
    else:
        raw.loc[0, "race_date"] = "2006-12-31"
    with pytest.raises(ValueError):
        core.mask_same_day(frames, raw, day="2026-10-25")


def test_current_replay_rejects_old_quote_post_and_late_predictions():
    snapshot, field = example_quote()
    quote = core.saved_quote(snapshot, field, [])
    evidence = core.current_admission(
        quote,
        post_time="2026-10-25T05:50:00+00:00",
        at="2026-10-27T01:00:00+00:00",
        ready_times=["2026-10-27T00:59:59+00:00"] * 2,
    )
    assert evidence["evidence_admissible"] is evidence["can_adopt_model"] is False
    assert evidence["comparison_data_added"] == 0
    assert {
        "quote_not_within_30_minutes",
        "race_already_started",
        "both_predictions_not_saved_before_historical_decision",
        "raw_http_and_fetch_interval_not_saved",
    } <= set(evidence["reasons"])
    assert evidence["original_quote_captured_at"] == snapshot["captured_at"]


@pytest.mark.parametrize(
    "seconds,stale", [(0, False), (1800, False), (1800.000001, True), (-0.000001, True)]
)
def test_fresh30_boundary_does_not_make_replay_admissible(seconds, stale):
    snapshot, field = example_quote()
    quote = core.saved_quote(snapshot, field, [])
    now = core.c.time(snapshot["captured_at"]) + dt.timedelta(seconds=seconds)
    result = core.current_admission(
        quote, post_time="2026-10-25T07:00:00+00:00", at=now.isoformat()
    )
    assert ("quote_not_within_30_minutes" in result["reasons"]) is stale
    assert result["evidence_admissible"] is False
    assert "historical_asof_features_not_proven" in result["reasons"]


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _read(path):
    return json.loads(path.read_text())


def _rewrite(root, name, value):
    path = root / name
    path.write_bytes(_canonical(value))
    manifest = _read(root / "manifest.json")
    manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "manifest.json").write_bytes(_canonical(manifest))


@pytest.fixture(scope="module")
def completed_artifact():
    # The frozen runtime artifact is a saved input; these tests never start the benchmark.
    path = Path(
        os.getenv(
            "MANUAL166_REVIEW_ARTIFACT", "artifacts/166-readonly-db-runtime/run-03"
        )
    )
    assert (path / "manifest.json").exists(), (
        "runtime artifact must finish before independent attacks"
    )
    return path


@pytest.fixture
def artifact(completed_artifact, tmp_path, monkeypatch):
    from scripts.roi_explore.db_runtime_166 import run

    shutil.copytree(completed_artifact, tmp_path / "packet")
    root = tmp_path / "packet"
    # The attack is about internal integrity, not edits made to source during the review.
    monkeypatch.setattr(
        run, "source_pins", lambda: _read(root / "protocol.json")["source_pins"]
    )
    return root, run


def test_unmodified_current_replay_verifies(artifact):
    root, run = artifact
    assert run.verify(root)["evidence_admissible"] is False


def test_receipt_cannot_precede_model_computation(artifact):
    root, run = artifact
    name = "prediction_mev.json.ready.json"
    ready = _read(root / name)
    ready["available_by_at"] = _read(root / "features.json.ready.json")[
        "available_by_at"
    ]
    _rewrite(root, name, ready)
    with pytest.raises(ValueError):
        run.verify(root)


def test_prediction_start_cannot_precede_feature_ready(artifact):
    root, run = artifact
    name = "prediction_legacy.json"
    payload = _read(root / name)
    payload["started_at"] = _read(root / "protocol.json")["registered_at"]
    _rewrite(root, name, payload)
    receipt = _read(root / (name + ".ready.json"))
    receipt["sha256"] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    _rewrite(root, name + ".ready.json", receipt)
    with pytest.raises(ValueError):
        run.verify(root)


def test_timing_cannot_claim_incorrect_elapsed_after_manifest_update(artifact):
    root, run = artifact
    timing = _read(root / "timing.json")
    timing["current_replay_core_seconds"] = 0.000001
    timing["within_preparation_budget_360_seconds"] = True
    _rewrite(root, "timing.json", timing)
    with pytest.raises(ValueError):
        run.verify(root)


def test_sql_hash_is_verified_even_if_manifest_is_updated(artifact):
    root, run = artifact
    audit = _read(root / "database-audit.json")
    audit["statements"][0]["sha256"] = "0" * 64
    _rewrite(root, "database-audit.json", audit)
    with pytest.raises(ValueError):
        run.verify(root)


def test_replay_mask_never_claims_historical_reconstruction(artifact):
    root, run = artifact
    mask = _read(root / "replay-mask.json")
    mask["historical_asof_reconstruction"] = True
    _rewrite(root, "replay-mask.json", mask)
    receipt = _read(root / "replay-mask.json.ready.json")
    receipt["sha256"] = hashlib.sha256(
        (root / "replay-mask.json").read_bytes()
    ).hexdigest()
    _rewrite(root, "replay-mask.json.ready.json", receipt)
    with pytest.raises(ValueError):
        run.verify(root)


def test_model_consumed_feature_matrix_cannot_be_changed(artifact):
    root, run = artifact
    name = "prediction_mev.json"
    payload = _read(root / name)
    consumed = payload["result"]["evidence"]["consumed_input"]
    index = consumed["columns"].index("odds")
    consumed["rows"][0][index] = 9999.0
    payload["result"]["evidence"]["consumed_input_sha256"] = hashlib.sha256(
        _canonical(consumed)
    ).hexdigest()
    _rewrite(root, name, payload)
    receipt = _read(root / (name + ".ready.json"))
    receipt["sha256"] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    _rewrite(root, name + ".ready.json", receipt)
    with pytest.raises(ValueError):
        run.verify(root)


def test_timing_phase_stamp_must_exist_in_real_clock_log(artifact):
    root, run = artifact
    clock = _read(root / "clock.json")
    timing = _read(root / "timing.json")
    removed = timing["phases"][0]["started_at"]
    clock["observations"] = [r for r in clock["observations"] if r["at"] != removed]
    _rewrite(root, "clock.json", clock)
    with pytest.raises(ValueError):
        run.verify(root)


def test_current_replay_cannot_claim_model_adoption(artifact):
    root, run = artifact
    status = _read(root / "status.json")
    status["can_adopt_model"] = True
    _rewrite(root, "status.json", status)
    with pytest.raises(ValueError):
        run.verify(root)


def test_protocol_cannot_relax_quote_freshness(artifact):
    root, run = artifact
    protocol = _read(root / "protocol.json")
    protocol["max_quote_age_seconds"] = 3600
    _rewrite(root, "protocol.json", protocol)
    with pytest.raises(ValueError):
        run.verify(root)


@pytest.mark.parametrize(
    "mutation",
    [
        "query_sha",
        "adjacency",
        "query_type",
        "sqlstate",
        "next_phase",
        "extra_parameter",
    ],
)
def test_known_metadata_probe_cannot_hide_unrelated_sql_failure(artifact, mutation):
    root, run = artifact
    audit = _read(root / "database-audit.json")
    index = next(
        i
        for i, r in enumerate(audit["statements"])
        if r["status"] == "handled_metadata_probe"
    )
    row = audit["statements"][index]
    if mutation == "query_sha":
        row["known_probe_candidate"]["expected_query_sha256"] = "0" * 64
    elif mutation == "adjacency":
        row["handled_by_statement_index"] = index + 2
    elif mutation == "query_type":
        row["known_probe_candidate"]["query_parameter_type"] = "builtins.str"
    elif mutation == "sqlstate":
        row["sqlstate"] = "42601"
    elif mutation == "next_phase":
        audit["statements"][index + 1]["phase"] = "another_phase"
    elif mutation == "extra_parameter":
        row["parameter_types"]["unrecognized"] = "builtins.str"
    _rewrite(root, "database-audit.json", audit)
    with pytest.raises(ValueError):
        run.verify(root)
