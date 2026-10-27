"""Direct-file audit of 166. Imports no 166 or 165 implementation and opens no database."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "independent_native_163",
    ROOT
    / "specs/163-offline-model-connection/evidence/independent_connection_audit.py",
)
NATIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NATIVE)
load, sha, digest, at, fields = (
    NATIVE.load,
    NATIVE.sha,
    NATIVE.digest,
    NATIVE.at,
    NATIVE.fields,
)

# The frozen 163 helper predates pandas 3 string dtype. Its categorical equality
# check needs null represented as None on BOTH sides, rather than StringDtype
# preserving NaN after where(..., None). Normalize only the in-memory consumed
# text columns; category vocabularies/model matrices/evidence bytes stay intact.
_native_matrix = NATIVE.matrix


def _matrix_with_object_nulls(evidence):
    frame = _native_matrix(evidence)
    for name in frame.columns:
        if isinstance(frame[name].dtype, NATIVE.pd.StringDtype):
            frame[name] = frame[name].astype(object).where(frame[name].notna(), None)
    return frame


NATIVE.matrix = _matrix_with_object_nulls


def audit(root):
    root = Path(root)
    manifest = load(root / "manifest.json")
    assert set(manifest["files"]) | {"manifest.json"} == {
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    }
    for name, checksum in manifest["files"].items():
        assert sha(root / name) == checksum, name
    protocol, status, clock, timing, sql = [
        load(root / name)
        for name in (
            "protocol.json",
            "status.json",
            "clock.json",
            "timing.json",
            "database-audit.json",
        )
    ]
    assert protocol["mode"] == status["mode"] == "readonly_db_runtime_replay"
    assert protocol["origin"] == status["origin"] == "saved_db_current_replay"
    assert status["state"] == "completed_current_replay"
    assert (
        protocol["new_collection"]
        is status["new_collection"]
        is status["database_write"]
        is status["evidence_admissible"]
        is status["can_adopt_model"]
        is False
    )
    assert protocol["comparison_data_added"] == status["comparison_data_added"] == 0
    assert (
        not (root / "accepted.json").exists() and not (root / "decision.json").exists()
    )
    for name, checksum in protocol["source_pins"].items():
        assert sha(ROOT / name) == checksum, name
    for name, checksum in protocol["model_protocol"]["model_files"].items():
        assert sha(ROOT / name) == checksum, name
    assert clock["simulated_clock"] is False
    observations = clock["observations"]
    assert len({r["epoch_id"] for r in observations}) == 1
    for before, after in zip(observations, observations[1:]):
        wall = (at(after["at"]) - at(before["at"])).total_seconds()
        mono = (after["monotonic_ns"] - before["monotonic_ns"]) / 1e9
        assert wall >= 0 and mono >= 0 and abs(wall - mono) <= 1
    stamps = {r["at"] for r in observations}
    for path in root.rglob("*.ready.json"):
        receipt = load(path)
        assert receipt["sha256"] == sha(root / receipt["path"])
        assert receipt["available_by_at"] in stamps
        assert (
            receipt["mode"] == protocol["mode"]
            and receipt["origin"] == protocol["origin"]
        )
        assert receipt["evidence_admissible"] is False
    assert sql["new_collection"] is sql["database_write"] is False
    handled_probes = 0
    for index, row in enumerate(sql["statements"]):
        assert hashlib.sha256(row["sql"].encode()).hexdigest() == row["sha256"]
        assert (
            row["status"] in ("completed", "handled_metadata_probe")
            and row["elapsed_seconds"] >= 0
        )
        if row["status"] == "handled_metadata_probe":
            handled_probes += 1
            assert (
                row["sha256"]
                == "3abe6a141a1bbb7060bedef89c62e34e818f1bc921dfa7aaab17b640e38baa37"
            )
            assert row["sqlstate"] is None and row["error_class"] == "ProgrammingError"
            candidate = row["known_probe_candidate"]
            assert candidate["kind"] == "pandas_read_sql_has_table_clause_adaptation"
            assert candidate["query_parameter_type"] in (
                "sqlalchemy.sql.selectable.Select",
                "sqlalchemy.sql.elements.TextClause",
            )
            assert (
                row["parameter_types"]["table_name"]
                == candidate["query_parameter_type"]
            )
            assert (
                set(row["parameter_keys"])
                == set(row["parameter_types"])
                == {
                    "table_name",
                    "nspname_1",
                    "param_1",
                    "param_2",
                    "param_3",
                    "param_4",
                    "param_5",
                }
            )
            assert all(
                value == "builtins.str"
                for name, value in row["parameter_types"].items()
                if name != "table_name"
            )
            next_row = sql["statements"][index + 1]
            assert (
                next_row["status"] == "completed" and row["phase"] == next_row["phase"]
            )
            assert (
                candidate["expected_query_sha256"]
                == row["handled_by_query_sha256"]
                == next_row["sha256"]
            )
            assert row["handled_by_statement_index"] == index + 1
        assert at(row["started_at"]) <= at(row["completed_at"])
        assert isinstance(row["parameter_keys"], list)
        normalized = " ".join(row["sql"].upper().split())
        assert normalized.startswith(
            ("SELECT ", "SHOW ", "SET TRANSACTION ", "SET LOCAL ")
        ), normalized
        assert "COMMIT" not in normalized and "ROLLBACK" not in normalized
    assert sql["snapshots"]
    for snap in sql["snapshots"]:
        assert (
            snap["proof"]["default_readonly"]
            == snap["proof"]["transaction_readonly"]
            == "on"
        )
        assert (
            snap["proof"]["isolation"] == "repeatable read"
            and snap["rolled_back"] is True
        )
        assert at(snap["started_at"]) <= at(snap["finished_at"])
    network = load(root / "network-boundary.json")
    assert network["attempts"] == [] and network["python_network_blocked"] is True
    assert load(root / "connection-proof.json")["default_readonly"] == "on"
    quote, source, mask, features = [
        load(root / name)
        for name in (
            "quote.json",
            "saved-quote-source.json",
            "replay-mask.json",
            "features.json",
        )
    ]
    assert (
        quote["received_at"]
        == quote["original_captured_at"]
        == source["snapshot"]["captured_at"]
    )
    assert quote["original_fetch_started_at"] is None and quote["provider_at"] is None
    assert quote["full_raw_http_saved"] is quote["evidence_admissible"] is False
    assert digest(source["snapshot"]["field"]) == source["snapshot"]["content_digest"]
    assert quote["raw_sha256"] == digest(source["snapshot"])
    assert (
        mask["database_changed"]
        is mask["source_objects_changed"]
        is mask["historical_asof_reconstruction"]
        is mask["primary_eligible"]
        is False
    )
    assert mask["masked_mev_columns"] == [
        "finish_order",
        "result_status",
        "last_3f",
        "margin_sec",
    ]
    assert mask["removed_legacy_result_rows"] > 0 and mask["masked_mev_rows"] > 0
    assert features["source_evidence"]["current_replay_mask_sha256"] == digest(mask)
    assert features["source_evidence"]["historical_asof_reconstruction"] is False
    assert features["source_evidence"]["target_outcomes_absent"] is True
    assert features["evidence_admissible"] is features["can_adopt_model"] is False
    assert at(features["generated_at"]) <= at(
        load(root / "features.json.ready.json")["available_by_at"]
    )
    expected = fields(quote["rows"])
    assert len(expected) == 15
    assert (
        fields(features["rows"]["legacy"])
        == fields(features["rows"]["mev"])
        == expected
    )
    assert all(len(r["features"]) == 138 for r in features["rows"]["legacy"])
    assert all(len(r["features"]) == 77 for r in features["rows"]["mev"])
    for r in features["rows"]["legacy"]:
        assert all(
            r["features"][k] is None
            for k in ("weight", "weight_diff", "carried_weight_ratio")
        )
    mapped_quote = {r["horse_id"]: r for r in quote["rows"]}
    for row in features["rows"]["mev"]:
        assert float(row["features"]["odds"]) == float(
            mapped_quote[row["horse_id"]]["odds"]
        )
        assert (
            row["features"]["popularity"] == mapped_quote[row["horse_id"]]["popularity"]
        )
    predictions = {}
    ready_times = []
    for kind in ("legacy", "mev"):
        p = load(root / ("prediction_" + kind + ".json"))
        ready = load(root / ("prediction_" + kind + ".json.ready.json"))
        assert p["feature_sha256"] == digest(features)
        assert p["evidence_admissible"] is False
        assert {p["input_available_at"], p["started_at"], p["computed_at"]} <= stamps
        assert (
            at(load(root / "features.json.ready.json")["available_by_at"])
            <= at(p["input_available_at"])
            <= at(p["started_at"])
            <= at(p["computed_at"])
            <= at(ready["available_by_at"])
        )
        ready_times.append(at(ready["available_by_at"]))
        predictions[kind] = dict(
            evidence=p["result"]["evidence"],
            rows=[
                dict(**r, probability=p["result"]["probabilities"][r["horse_id"]])
                for r in features["rows"][kind]
            ],
        )
        warm = root / ("prediction_" + kind + "_warm.json")
        if warm.exists():
            assert load(warm)["result"] == p["result"]
    native = NATIVE.actual_native_predictions(
        dict(race_date=protocol["feature_pool_through"], predictions=predictions)
    )
    admission = load(root / "admission.json")
    assert admission["evidence_admissible"] is admission["can_adopt_model"] is False
    assert admission["comparison_data_added"] == 0
    assert max(ready_times) <= at(admission["at"])
    age = (at(admission["at"]) - at(quote["original_captured_at"])).total_seconds()
    assert math.isclose(age, admission["quote_age_seconds"], abs_tol=1e-6)
    assert age > 1800 and at(admission["at"]) > at(admission["post_time"])
    assert {
        "quote_not_within_30_minutes",
        "race_already_started",
        "both_predictions_not_saved_before_historical_decision",
    } <= set(admission["reasons"])
    for stage in ("before", "after", "final"):
        value = load(root / (stage + ".json"))
        assert value["rejection"]["rejected"] is True
        assert (
            fields(
                [
                    r
                    for r in value["observations"]["entries"]["rows"]
                    if r["entry_status"] == "started"
                ]
            )
            == expected
        )
        assert len(value["observations"]["results"]["rows"]) == 15
        actual_age = max(
            (at(value["checked_at"]) - at(q["started_at"])).total_seconds()
            for q in value["observations"].values()
        )
        assert math.isclose(
            actual_age, value["guard_snapshot_age_seconds"], abs_tol=1e-9
        )
        assert value["within_five_seconds"] == (actual_age <= 5)
    assert (
        load(root / "production-reader-rejection.json")["reason"]
        == "target_results_already_present"
    )
    phases = timing["phases"]
    for index, row in enumerate(phases, 1):
        assert row["status"] == "completed"
        assert row["started_at"] in stamps and row["completed_at"] in stamps
        assert row["completed_perf_ns"] >= row["started_perf_ns"]
        assert math.isclose(
            (row["completed_perf_ns"] - row["started_perf_ns"]) / 1e9,
            row["elapsed_seconds"],
            abs_tol=1e-12,
        )
        assert (
            abs(
                (at(row["completed_at"]) - at(row["started_at"])).total_seconds()
                - row["elapsed_seconds"]
            )
            < 1
        )
        assert load(root / "progress" / f"{index:02d}-{row['name']}-end.json") == row
    total = (timing["finished_perf_ns"] - timing["process_start_perf_ns"]) / 1e9
    assert math.isclose(total, timing["elapsed_process_seconds"], abs_tol=1e-12)
    assert timing["elapsed_process_seconds"] == status["elapsed_process_seconds"]
    assert timing["real_http_fetch_and_rate_limit_wait_measured"] is False
    assert timing["full_history_loaded"] is True
    # Core spans the production guarded replay up to admission, with warm/official work separate.
    main = [p for p in phases if p["name"] == "before_guard"][0]
    end = [p for p in phases if p["name"] == "admission_rejection_and_save"][0]
    lower = (end["completed_perf_ns"] - main["started_perf_ns"]) / 1e9
    assert timing["current_replay_core_seconds"] >= lower
    assert timing["current_replay_core_seconds"] - lower < 1
    startup_to_core = (
        timing["core_end_perf_ns"] - timing["process_start_perf_ns"]
    ) / 1e9
    assert math.isclose(
        startup_to_core, timing["startup_to_core_finish_seconds"], abs_tol=1e-12
    )
    assert timing["within_preparation_budget_360_seconds"] == (startup_to_core <= 360)
    assert timing["final_report_and_manifest_writes_included"] is False
    assert timing["filesystem_cache_controlled"] is False
    payout = load(root / "current-db-official-payouts.json")
    assert (
        payout["current_replay_valued"] is False
        and payout["payout_yen"] is payout["profit_yen"] is None
    )
    historical = NATIVE.historical_replay(root)
    return dict(
        status="independently_verified",
        new_collection=False,
        database_write=False,
        comparison_data_added=0,
        actual_db_access=True,
        source_pins_verified=True,
        sql_statement_hashes_verified=True,
        strictly_classified_pandas_metadata_probes=handled_probes,
        unhandled_sql_failures=0,
        clock_and_ready_order_verified=True,
        original_quote_clock_preserved=True,
        raw_http_missing_remains_ineligible=True,
        masked_current_replay_not_historical_asof=True,
        actual_models=native,
        full_history_counts=load(root / "source.json")["source_counts"],
        elapsed_process_seconds=total,
        current_replay_core_seconds=timing["current_replay_core_seconds"],
        peak_process_rss_bytes=timing["peak_process_rss_bytes"],
        historical_official=historical,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
