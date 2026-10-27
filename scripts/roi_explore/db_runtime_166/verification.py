"""Offline consistency audit of current saved-DB runtime measurements."""

from __future__ import annotations

from pathlib import Path

from .core import (
    MODE,
    ORIGIN,
    TARGET,
    GUARD_TARGETS,
    SNAPSHOT_ID,
    c,
    models,
    current_admission,
    saved_quote,
)
from .database import validate_statement
from scripts.roi_explore.model_connection_163.clock import verify_observations
from scripts.roi_explore.prospective_rehearsal_162.store import read


def safe_path(root, name):
    relative = Path(name)
    c.require(
        not relative.is_absolute() and ".." not in relative.parts, "runtime_path_unsafe"
    )
    path = root
    for part in relative.parts:
        path /= part
        c.require(not path.is_symlink(), "runtime_path_unsafe")
    return path


def close(a, b):
    return isinstance(a, (float, int)) and abs(a - b) < 1e-8


def verify_timing(root, timing, status, stamps):
    process, run, finish = (
        timing[k]
        for k in ("process_start_perf_ns", "run_start_perf_ns", "finished_perf_ns")
    )
    c.require(0 <= process <= run <= finish, "timing_process_order")
    c.require(
        close(timing["module_import_startup_seconds"], (run - process) / 1e9)
        and close(timing["elapsed_process_seconds"], (finish - process) / 1e9)
        and close(status["elapsed_process_seconds"], timing["elapsed_process_seconds"]),
        "timing_process_changed",
    )
    previous = run
    labels = {}
    for index, row in enumerate(timing["phases"], 1):
        c.require(row["name"] not in labels, "timing_phase_duplicate")
        labels[row["name"]] = row
        c.require(
            previous <= row["started_perf_ns"] <= row["completed_perf_ns"] <= finish
            and close(
                row["elapsed_seconds"],
                (row["completed_perf_ns"] - row["started_perf_ns"]) / 1e9,
            )
            and {row["started_at"], row["completed_at"]} <= stamps,
            "timing_phase_changed",
        )
        wall = (c.time(row["completed_at"]) - c.time(row["started_at"])).total_seconds()
        c.require(abs(wall - row["elapsed_seconds"]) <= 1, "timing_wall_disagrees")
        prefix = f"{index:02d}-" + row["name"]
        c.require(
            read(root / "progress" / (prefix + "-end.json")) == row,
            "timing_phase_ledger_changed",
        )
        initial = read(root / "progress" / (prefix + "-start.json"))
        c.require(
            initial == {k: row[k] for k in ("name", "started_at", "started_perf_ns")},
            "timing_phase_start_changed",
        )
        previous = row["completed_perf_ns"]
    full = all(
        labels.get(name, {}).get("status") == "completed"
        for name in ("load_full_legacy_pool", "load_full_mev_pool")
    )
    c.require(timing["full_history_loaded"] == full, "history_load_claim_changed")
    a, b = timing["core_start_perf_ns"], timing["core_end_perf_ns"]
    if b is None:
        c.require(
            timing["current_replay_core_seconds"] is None
            and timing["within_preparation_budget_360_seconds"] is None,
            "incomplete_core_timing_claim",
        )
    else:
        c.require(
            run <= a <= b <= finish
            and close(timing["current_replay_core_seconds"], (b - a) / 1e9)
            and close(timing["startup_to_core_finish_seconds"], (b - process) / 1e9)
            and timing["within_preparation_budget_360_seconds"]
            == ((b - process) / 1e9 <= 360),
            "timing_core_changed",
        )
        c.require(
            0 <= labels["before_guard"]["started_perf_ns"] - a <= 100_000_000
            and 0
            <= b - labels["admission_rejection_and_save"]["completed_perf_ns"]
            <= 100_000_000,
            "timing_core_phase_binding",
        )
    c.require(
        timing["final_report_and_manifest_writes_included"] is False
        and timing["real_http_fetch_and_rate_limit_wait_measured"] is False
        and timing["filesystem_cache_controlled"] is False,
        "timing_scope_changed",
    )


def verify_replay(root, stamps, receipts):
    quote, source, features, mask, admission = [
        read(root / name)
        for name in (
            "quote.json",
            "saved-quote-source.json",
            "features.json",
            "replay-mask.json",
            "admission.json",
        )
    ]
    before = read(root / "before.json")
    field = [
        {k: r[k] for k in ("horse_id", "horse_number")}
        for r in before["observations"]["entries"]["rows"]
        if r["entry_status"] == "started"
    ]
    c.require(
        quote == saved_quote(source["snapshot"], field, source["mappings"]),
        "saved_quote_replay_changed",
    )
    c.require(
        mask["historical_asof_reconstruction"] is False
        and mask["primary_eligible"] is False
        and mask["database_changed"] is False
        and mask["source_objects_changed"] is False
        and mask["masked_mev_columns"]
        == ["finish_order", "result_status", "last_3f", "margin_sec"]
        and features["source_evidence"]["current_replay_mask_sha256"] == c.digest(mask)
        and features["source_evidence"]["historical_asof_reconstruction"] is False,
        "replay_mask_changed",
    )
    c.require(
        features["mode"] == MODE
        and features["origin"] == ORIGIN
        and features["evidence_admissible"] is False
        and features["can_adopt_model"] is False
        and features["generated_at"] in stamps
        and c.time(features["generated_at"]) <= c.time(receipts["features.json"]),
        "feature_replay_identity_or_clock",
    )
    expected = c.field(field)
    derived = c.market_features(quote["rows"])
    for row in features["rows"]["mev"]:
        for key, value in derived[row["horse_id"]].items():
            if key in row["features"]:
                c.require(
                    close(float(row["features"][key]), float(value)),
                    "mev_quote_input_changed",
                )
    ready_times = []
    latest_compute = None
    for kind in ("legacy", "mev"):
        rows = features["rows"][kind]
        c.require(c.field(rows) == expected, "feature_field_changed")
        predictions = []
        for suffix in ("", "_warm"):
            name = "prediction_" + kind + suffix + ".json"
            if not (root / name).exists():
                continue
            p = read(root / name)
            c.require(
                p["feature_sha256"] == c.digest(features)
                and p["mode"] == MODE
                and p["origin"] == ORIGIN
                and p["evidence_admissible"] is False,
                "prediction_input_changed",
            )
            times = (
                [receipts["features.json"]]
                + [p[k] for k in ("input_available_at", "started_at", "computed_at")]
                + [receipts[name]]
            )
            c.require(
                all(t in stamps for t in times)
                and all(c.time(x) <= c.time(y) for x, y in zip(times, times[1:])),
                "prediction_save_clock_order",
            )
            binding = dict(
                evidence=p["result"]["evidence"],
                rows=[
                    dict(**r, probability=p["result"]["probabilities"][r["horse_id"]])
                    for r in rows
                ],
            )
            models.validate_evidence_binding(
                kind, binding, race_id=TARGET, race_date="2026-10-25"
            )
            if kind == "legacy":
                frame, _ = models._input_frame(rows, TARGET, "2026-10-25", "legacy")
                expected_history = models._frame_evidence(
                    models._history(frame, features["legacy_history"], "2026-10-25")
                )
                c.require(
                    p["result"]["evidence"]["started_history"] == expected_history,
                    "legacy_source_history_changed",
                )
            predictions.append(p["result"])
            if not suffix:
                ready_times.append(receipts[name])
                latest_compute = max(
                    latest_compute or c.time(p["computed_at"]), c.time(p["computed_at"])
                )
        c.require(
            bool(predictions) and all(p == predictions[0] for p in predictions),
            "warm_prediction_changed",
        )
    for name in ("before", "after", "final"):
        guard = read(root / (name + ".json"))
        duration = max(
            (c.time(guard["checked_at"]) - c.time(q["started_at"])).total_seconds()
            for q in guard["observations"].values()
        )
        c.require(
            close(guard["guard_snapshot_age_seconds"], duration)
            and guard["within_five_seconds"] == (duration <= 5),
            "guard_age_claim_changed",
        )
        c.require(guard["rejection"]["rejected"] is True, "historical_guard_admitted")
        observed_field = [
            {k: r[k] for k in ("horse_id", "horse_number")}
            for r in guard["observations"]["entries"]["rows"]
            if r["entry_status"] == "started"
        ]
        c.require(c.field(observed_field) == expected, "guard_field_changed")
        for query in guard["observations"].values():
            c.require(
                {query["started_at"], query["completed_at"], guard["checked_at"]}
                <= stamps
                and c.time(query["started_at"])
                <= c.time(query["completed_at"])
                <= c.time(guard["checked_at"]),
                "guard_clock_changed",
            )
            if name == "after":
                c.require(
                    c.time(query["started_at"]) >= latest_compute,
                    "after_before_computation",
                )
            if name == "final":
                c.require(
                    c.time(query["started_at"]) >= max(map(c.time, ready_times)),
                    "final_before_predictions_saved",
                )
    post = before["observations"]["race"]["rows"][0]["post_time"]
    c.require(
        admission
        == current_admission(
            quote, post_time=post, at=admission["at"], ready_times=ready_times
        )
        and admission["at"] in stamps
        and all(c.time(t) <= c.time(admission["at"]) for t in ready_times),
        "admission_replay_changed",
    )
    payout = read(root / "current-db-official-payouts.json")
    c.require(
        payout["current_replay_valued"] is False
        and payout["payout_yen"] is None
        and payout["profit_yen"] is None,
        "replay_cannot_be_settled",
    )
    c.require(
        read(root / "network-boundary.json")["attempts"] == [],
        "network_attempts_present",
    )


def verify_packet(directory, *, expected_source_pins):
    root = Path(directory)
    manifest = read(root / "manifest.json")
    c.require(
        manifest["mode"] == MODE and manifest["origin"] == ORIGIN,
        "manifest_identity_changed",
    )
    c.require(
        set(manifest["files"]) | {"manifest.json"}
        == {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()},
        "runtime_file_inventory_changed",
    )
    for name, sha in manifest["files"].items():
        c.require(
            c.sha_bytes(safe_path(root, name).read_bytes()) == sha,
            "runtime_file_changed",
        )
    protocol = read(root / "protocol.json")
    c.require(
        protocol["mode"] == MODE
        and protocol["origin"] == ORIGIN
        and protocol["source_pins"] == expected_source_pins,
        "runtime_source_changed",
    )
    c.require(
        protocol["target_race_id"] == TARGET
        and protocol["diagnostic_guard_targets"] == list(GUARD_TARGETS)
        and protocol["saved_quote_id"] == SNAPSHOT_ID
        and protocol["manual_only"] is True
        and protocol["new_collection"] is False
        and protocol["database_write"] is False
        and protocol["evidence_admissible"] is False
        and protocol["can_adopt_model"] is False
        and protocol["comparison_data_added"] == 0
        and protocol["max_quote_age_seconds"] == 1800
        and protocol["live_preparation_budget_seconds"] == 360
        and protocol["live_guard_budget_seconds"] == 5
        and protocol["live_decision_window_seconds"] == 60
        and protocol["feature_pool_since"] == "2007-01-01"
        and protocol["feature_pool_through"] == "2026-10-25",
        "fixed_runtime_contract_changed",
    )
    c.require(protocol["model_protocol"] == c.make_protocol(), "runtime_model_changed")
    status = read(root / "status.json")
    c.require(
        status["state"] in ("failed_runtime", "completed_current_replay")
        and status["mode"] == MODE
        and status["origin"] == ORIGIN
        and status["evidence_admissible"] is False
        and status["comparison_data_added"] == 0
        and status["new_collection"] is False
        and status["database_write"] is False
        and status["can_adopt_model"] is False,
        "replay_admission_changed",
    )
    c.require(
        not (root / "accepted.json").exists() and not (root / "decision.json").exists(),
        "replay_cannot_be_live",
    )
    log = read(root / "clock.json")
    c.require(log["simulated_clock"] is False, "clock_label_changed")
    verify_observations(log["observations"])
    stamps = {r["at"] for r in log["observations"]}
    receipts = {}
    for path in root.rglob("*.ready.json"):
        r = read(path)
        c.require(
            path.relative_to(root).as_posix() == r["path"] + ".ready.json"
            and r["sha256"] == c.sha_bytes(safe_path(root, r["path"]).read_bytes())
            and r["available_by_at"] in stamps
            and r["mode"] == MODE
            and r["origin"] == ORIGIN
            and r["evidence_admissible"] is False,
            "runtime_ready_changed",
        )
        receipts[r["path"]] = r["available_by_at"]
    audit = read(root / "database-audit.json")
    for index, row in enumerate(audit["statements"]):
        validate_statement(row["sql"])
        c.require(row["sha256"] == c.sha_bytes(row["sql"].encode()), "sql_hash_changed")
        if row["status"] == "handled_metadata_probe":
            from .database import (
                METADATA_PROBE_SQL_SHA256,
                METADATA_PROBE_PARAMETER_KEYS,
            )

            candidate = row["known_probe_candidate"]
            next_row = audit["statements"][index + 1]
            allowed_types = (
                "sqlalchemy.sql.selectable.Select",
                "sqlalchemy.sql.elements.TextClause",
            )
            c.require(
                row["sha256"] == METADATA_PROBE_SQL_SHA256
                and row["error_class"] == "ProgrammingError"
                and row["sqlstate"] is None
                and candidate["kind"] == "pandas_read_sql_has_table_clause_adaptation"
                and candidate["query_parameter_type"] in allowed_types
                and row["parameter_types"]["table_name"]
                == candidate["query_parameter_type"]
                and set(row["parameter_keys"])
                == set(row["parameter_types"])
                == METADATA_PROBE_PARAMETER_KEYS
                and all(
                    t == "builtins.str"
                    for key, t in row["parameter_types"].items()
                    if key != "table_name"
                )
                and next_row["status"] == "completed"
                and next_row["phase"] == row["phase"]
                and next_row["sha256"]
                == row["handled_by_query_sha256"]
                == candidate["expected_query_sha256"]
                and row["handled_by_statement_index"] == index + 1,
                "metadata_probe_claim_changed",
            )
    for snapshot in audit["snapshots"]:
        if "proof" in snapshot:
            proof = snapshot["proof"]
            c.require(
                proof["default_readonly"] == proof["transaction_readonly"] == "on"
                and proof["isolation"] == "repeatable read"
                and snapshot["rolled_back"] is True,
                "readonly_proof_changed",
            )
    for tx in audit["transactions"]:
        c.require(
            tx["event"] in ("rollback", "commit_readonly", "commit_rejected"),
            "transaction_kind_changed",
        )
        if tx["event"] == "commit_readonly":
            p = tx["proof"]
            c.require(
                p["default_readonly"] == p["transaction_readonly"] == "on"
                and p["isolation"] == "repeatable read",
                "readonly_commit_changed",
            )
    verify_timing(root, read(root / "timing.json"), status, stamps)
    if status["state"] == "completed_current_replay":
        c.require(
            all(
                row["status"] in ("completed", "handled_metadata_probe")
                for row in audit["statements"]
            ),
            "completed_sql_failure",
        )
        verify_replay(root, stamps, receipts)
    return dict(
        status="verified",
        state=status["state"],
        mode=MODE,
        evidence_admissible=False,
        database_write=False,
        new_collection=False,
        comparison_data_added=0,
    )
