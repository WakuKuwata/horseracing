"""Direct-file final audit, never imports 168/165 execution or queries a database."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def when(value):
    stamp = dt.datetime.fromisoformat(value)
    assert stamp.tzinfo is not None
    return stamp


def audit(folder):
    folder = Path(folder).resolve()
    manifest = load(folder / "manifest.json")
    assert manifest["state"] == "NO_GO_SKIP"
    assert manifest["database_write"] is manifest["new_collection"] is False
    assert set(manifest["files"]) | {"manifest.json"} == {
        str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()
    }
    for name, checksum in manifest["files"].items():
        relative = Path(name)
        assert not relative.is_absolute() and ".." not in relative.parts
        assert sha(folder / relative) == checksum
    input_pins = load(folder / "input-pins.json")
    for group in ("input_refs", "source_pins"):
        for name, checksum in input_pins[group].items():
            assert sha(ROOT / name) == checksum
    snapshot, access, assessment, pins, operator = [
        load(folder / name)
        for name in (
            "snapshot.json",
            "access.json",
            "assessment.json",
            "dependency-pins.json",
            "operator-record.json",
        )
    ]
    assert snapshot["actual_db_access"] is True
    assert (
        snapshot["database_write"]
        is snapshot["new_collection"]
        is snapshot["model_inference"]
        is False
    )
    assert snapshot["prices_or_prediction_values_read"] is False
    assert when(snapshot["as_of_utc"]) == when(snapshot["as_of_jst"])
    today = when(snapshot["as_of_jst"]).date().isoformat()
    coverage = snapshot["coverage"][0]
    assert coverage["races_total"] == 140049
    assert coverage["last_race_date"] == "2026-10-25" < today
    assert (
        coverage["races_today_or_later"]
        == coverage["future_post_rows"]
        == coverage["upcoming_missing_post"]
        == 0
    )
    assert (
        snapshot["date_coverage"]
        == snapshot["upcoming_races"]
        == snapshot["upcoming_entries"]
        == []
    )
    assert len(snapshot["latest_saved_day"]) == 1
    assert (
        snapshot["latest_saved_day"][0]["races"]
        == snapshot["latest_saved_day"][0]["races_with_results"]
        == 36
    )
    assert all(
        when(row["post_time"]) < when(snapshot["as_of_utc"]) and row["result_rows"] > 0
        for row in snapshot["recent_saved_reference"]
    )
    for doc in (snapshot, access):
        assert (
            doc["readonly_proof"]["default_readonly"]
            == doc["readonly_proof"]["transaction_readonly"]
            == "on"
        )
        assert doc["readonly_proof"]["isolation"] == "repeatable read"
        assert doc["network_boundary"]["attempts"] == []
        assert doc["network_boundary"]["python_network_blocked"] is True
    sql_counts = {}
    for name in ("database-audit.json", "access-database-audit.json"):
        doc = load(folder / name)
        assert doc["new_collection"] is doc["database_write"] is False
        assert doc["snapshots"] and all(row["rolled_back"] for row in doc["snapshots"])
        for row in doc["statements"]:
            assert hashlib.sha256(row["sql"].encode()).hexdigest() == row["sha256"]
            assert row["status"] == "completed"
            assert (
                row["sql"]
                .strip()
                .upper()
                .startswith(("SELECT ", "SET TRANSACTION READ ONLY"))
            )
        assert all(row["event"] == "rollback" for row in doc["transactions"])
        sql_counts[name] = len(doc["statements"])
    clock = load(folder / "clock.json")
    assert clock["simulated_clock"] is False
    for before, after in zip(clock["observations"], clock["observations"][1:]):
        wall = (when(after["at"]) - when(before["at"])).total_seconds()
        monotonic = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
        assert wall >= 0 and monotonic >= 0 and abs(wall - monotonic) <= 1
    local = load(OUT / "local-inventory.json")
    ledger = load(ROOT / local["local_ledger_ref"])
    assert sha(ROOT / local["local_ledger_ref"]) == local["local_ledger_sha256"]
    assert len(ledger) == 3665 and local["stored_card_races_at_or_after_today"] == 0
    assert (
        local["unparseable_own_date_count"] == 0
        and local["latest_own_race_date"] == "2026-10-25"
    )
    assert local["independent_race_list_url_markers"] == 0
    assert local["navigation_links_used_as_cards"] is False
    assert all(
        row["own_race_date"] < today and row["at_or_after_today"] is False
        for row in ledger
    )
    for row in ledger:
        assert sha(ROOT / row["archive_path"]) == row["archive_gz_sha256"]
        assert sha(ROOT / row["path"]) == row["sha256"]
    assert (
        assessment["state"] == "NO_GO_SKIP" and assessment["selected_race_id"] is None
    )
    assert (
        assessment["selected_post_time"] is None
        and assessment["operator_available_interval"] is None
    )
    assert (
        assessment["metadata_candidates"]
        == assessment["upcoming_db_rows"]
        == assessment["saved_future_card_count"]
        == 0
    )
    assert (
        load(folder / "candidate-ledger.json")
        == assessment["metadata_provisional_candidates"]
        == []
    )
    assert assessment["conditions"] == dict(
        quote_max_age_seconds=1800,
        both_predictions_durable_before_decision=True,
        decision_window_minutes_before_post=[10, 9],
        official_payout_only=True,
    )
    assert assessment["observe_invocations"] == assessment["comparison_data_added"] == 0
    for key in (
        "new_collection",
        "database_write",
        "live_plan_created",
        "model_inference",
        "official_settlement_executed",
        "slot_reserved",
        "evidence_admissible",
        "can_adopt_model",
        "candidate_signal_values_used",
        "real_world_meeting_calendar_inferred",
    ):
        assert assessment[key] is False, key
    gates = assessment["go_gates"]
    assert gates["eligible_saved_race"]["status"] == "BLOCKED"
    assert gates["stored_original_future_card"]["status"] == "BLOCKED"
    assert gates["operator_available_interval"]["status"] == "UNCONFIRMED"
    assert gates["live_database_configuration"]["status"] == "NOT_CONFIGURED"
    assert gates["robots_cache"]["status"] == "BLOCKED_NOW"
    assert all(
        gates[name]["status"] == "NOT_YET_DUE"
        for name in (
            "quote_age_at_decision",
            "both_predictions_durable_before_decision",
            "three_guards_and_decision_window",
            "official_results_and_payout",
        )
    )
    assert (
        access["live_database_url_configured"] is access["live_role_confirmed"] is False
    )
    assert (
        access["slot_reserved"]
        is access["cache_refreshed"]
        is access["cooldown_cleared"]
        is False
    )
    assert access["future_slot_available_at_launch"] is None
    robots = access["robots"]
    assert sha(robots["path"]) == robots["sha256"]
    assert (
        when(access["as_of_utc"]) - when(robots["original_fetched_at"])
    ).total_seconds() == robots["age_seconds"]
    assert when(robots["expires_at"]) <= when(access["as_of_utc"])
    assert robots["fresh_now"] is False and robots["valid_for_selected_launch"] is None
    for kind, key in (("source", "source_pins"), ("models", "model_pins")):
        assert pins["pin_checks"][kind]["mismatches"] == []
        for name, checksum in pins[key].items():
            assert sha(ROOT / name) == checksum
    assert pins["live_registry_exists"] is False and pins["reservations"] == []
    assert not (ROOT / "artifacts/manual_observation_registry_165").exists()
    assert operator["template_only"] is False and operator["state"] == "NO_GO_SKIP"
    assert (
        operator["new_collection_started"]
        is operator["start_instruction_received"]
        is False
    )
    assert operator["selection"]["selected_race_id"] is None
    assert operator["execution"]["observe_invocation_count"] == 0
    result = dict(
        status="independently_verified",
        artifact_manifest_sha256=sha(folder / "manifest.json"),
        all_artifact_file_hashes_verified=True,
        input_refs_and_execution_sources_verified=True,
        scope="direct saved-file audit; no independent DB requery",
        root_actual_db_access=True,
        independent_db_access=False,
        new_collection=False,
        database_write=False,
        model_inference=False,
        comparison_data_added=0,
        selected_race_id=None,
        decision="NO_GO_SKIP",
        saved_db_races=coverage["races_total"],
        latest_saved_db_day=coverage["last_race_date"],
        upcoming_db_races=0,
        stored_local_latest_card_races=len(ledger),
        stored_future_local_cards=0,
        sql_hashes_and_readonly_proofs_verified=True,
        sql_statement_counts=sql_counts,
        source_and_model_file_hashes_verified=True,
        quote_and_prediction_conditions_unchanged=True,
        missing_operator_window_and_live_configuration_not_invented=True,
        expired_robots_cache_not_refreshed=True,
        future_navigation_not_promoted=True,
        no_live_plan_or_registry=True,
        saved_inputs={
            str(p.relative_to(ROOT)): sha(p) for p in sorted(folder.glob("*.json"))
        },
    )
    (OUT / "independent-audit-run-01.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=ROOT / "artifacts/168-saved-candidate-go/run-01",
    )
    result = audit(parser.parse_args().artifact)
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "saved_inputs"},
            ensure_ascii=False,
            indent=2,
        )
    )
