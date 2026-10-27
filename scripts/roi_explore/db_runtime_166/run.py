"""Real saved-DB runtime replay; not a historical decision or an acquisition command."""

from __future__ import annotations

import contextlib
import datetime as dt
import resource
import time
from pathlib import Path

from .core import (
    MODE,
    ORIGIN,
    TARGET,
    GUARD_TARGETS,
    SNAPSHOT_ID,
    c,
    models,
    encode,
    saved_quote,
    mask_same_day,
    current_admission,
    safe_error,
)
from .database import create_readonly_engine, readonly_snapshot, database_only
from scripts.roi_explore.manual_observation_165.adapters import ObservationAdapters
from scripts.roi_explore.manual_observation_165.features import (
    DatabaseFeatureReader,
    build_from_snapshot,
)
from scripts.roi_explore.manual_observation_165.control import source_pins as pins165
from scripts.roi_explore.manual_observation_165.session import _field_guard
from scripts.roi_explore.model_connection_163.clock import (
    RealClock,
)
from scripts.roi_explore.model_connection_163.replay import historical_official_replay
from scripts.roi_explore.model_connection_163.run import runtime
from scripts.roi_explore.prospective_rehearsal_162.store import publish
from horseracing_features.loader import load_frames
from horseracing_features.builder import assemble_feature_matrix
from horseracing_features.materialize import source_fingerprint
from horseracing_training import market_ev
from sqlalchemy import text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
MAPPING_SQL = """SELECT source_id, canonical_id, mapping_status FROM id_mappings
WHERE entity_type='horse' AND source='netkeiba'
AND (source_id = ANY(:source_ids) OR canonical_id = ANY(:horse_ids))
ORDER BY source_id, canonical_id"""
QUOTE_SQL = """SELECT chaos_snapshot_id::text,race_id,captured_at,n,source,status,
content_digest,field,created_at FROM chaos_snapshots
WHERE race_id=:race_id AND chaos_snapshot_id=:snapshot_id"""
PAYOUT_SQL = """SELECT horse_number,payout_yen,source,observed_at,html_sha256
FROM official_win_payouts WHERE race_id=:race_id ORDER BY horse_number"""


def source_pins():
    result = pins165()
    result.update(
        {
            str(p.relative_to(ROOT)): c.sha_bytes(p.read_bytes())
            for p in Path(__file__).parent.glob("*.py")
        }
    )
    return result


class Recorder:
    def __init__(self, root, clock):
        self.root, self.clock, self.phases = Path(root), clock, []

    def save(self, name, value):
        checksum = publish(self.root / name, value)
        ready = dict(
            mode=MODE,
            origin=ORIGIN,
            path=name,
            sha256=checksum,
            available_by_at=self.clock.now(),
            evidence_admissible=False,
        )
        publish(self.root / (name + ".ready.json"), ready)
        return ready

    @contextlib.contextmanager
    def phase(self, label, audit):
        audit["phase"] = label
        row = dict(
            name=label,
            started_at=self.clock.now(),
            started_perf_ns=time.perf_counter_ns(),
        )
        self.phases.append(row)
        publish(
            self.root
            / "progress"
            / (f"{len(self.phases):02d}-" + label + "-start.json"),
            row,
        )
        try:
            yield row
            row["status"] = "completed"
        except BaseException as error:
            row.update(status="failed", **safe_error(error))
            raise
        finally:
            row.update(
                completed_at=self.clock.now(), completed_perf_ns=time.perf_counter_ns()
            )
            row["elapsed_seconds"] = (
                row["completed_perf_ns"] - row["started_perf_ns"]
            ) / 1e9
            publish(
                self.root
                / "progress"
                / (f"{len(self.phases):02d}-" + label + "-end.json"),
                row,
            )
            print(label, row["status"], f"{row['elapsed_seconds']:.3f}s", flush=True)


def expect_rejection(operation):
    try:
        operation()
    except ValueError as error:
        return dict(rejected=True, **safe_error(error))
    raise ValueError("historical_data_was_unexpectedly_admitted")


def collect_guard(adapter, stage, race_id, clock):
    observations = adapter.observe_guard(stage, race_id, clock)
    anchor = clock.now()
    race = observations["race"]["rows"]
    c.require(
        len(race) == 1 and race[0]["post_time"] is not None, "race_or_post_missing"
    )
    plan = dict(race[0])
    rejection = expect_rejection(
        lambda: _field_guard(observations, plan=plan, anchor=anchor)
    )
    duration = max(
        (c.time(anchor) - c.time(q["started_at"])).total_seconds()
        for q in observations.values()
    )
    return dict(
        observations=observations,
        checked_at=anchor,
        rejection=rejection,
        guard_snapshot_age_seconds=duration,
        within_five_seconds=duration <= 5,
    )


def _snapshot_inputs(engine, audit, recorder, field):
    day = dt.date(2026, 10, 25)
    with (
        readonly_snapshot(engine, audit) as (conn, proof),
        Session(bind=conn) as session,
    ):
        with recorder.phase("saved_quote_and_identity", audit):
            snapshot = encode(
                dict(
                    conn.execute(
                        text(QUOTE_SQL), dict(race_id=TARGET, snapshot_id=SNAPSHOT_ID)
                    )
                    .mappings()
                    .one()
                )
            )
            mappings = encode(
                [
                    dict(r)
                    for r in conn.execute(
                        text(MAPPING_SQL),
                        dict(
                            source_ids=[r["horse_id"] for r in snapshot["field"]],
                            horse_ids=[r["horse_id"] for r in field],
                        ),
                    )
                    .mappings()
                    .all()
                ]
            )
            quote = saved_quote(snapshot, field, mappings)
            recorder.save(
                "saved-quote-source.json",
                dict(
                    snapshot=snapshot,
                    mappings=mappings,
                    observed_now_at=recorder.clock.now(),
                    proof=proof,
                ),
            )
            recorder.save("quote.json", quote)
        with recorder.phase("load_full_legacy_pool", audit):
            frames = load_frames(session, end_date=day)
        with recorder.phase("load_full_mev_pool", audit):
            raw = market_ev.load_rows(conn, through=day.isoformat(), since="2007-01-01")
        with recorder.phase("snapshot_source_fingerprint", audit):
            fingerprint = source_fingerprint(frames, through=day)
            counts = dict(
                races=len(frames.races),
                horses=len(frames.horses),
                race_horses=len(frames.race_horses),
                race_results=len(frames.race_results),
                mev=len(raw),
            )
            source = dict(
                proof=proof,
                raw_legacy_source_fingerprint=fingerprint,
                source_counts=counts,
                acquired_by_at=recorder.clock.now(),
                historical_asof_reconstruction=False,
            )
            recorder.save("source.json", source)
        with recorder.phase("mask_historical_outcomes_in_copy", audit):
            clean_frames, clean_raw, mask = mask_same_day(frames, raw, day=day)
            mask["original_target_legacy_results"] = encode(
                frames.race_results.loc[frames.race_results.race_id.eq(TARGET)].to_dict(
                    "records"
                )
            )
            recorder.save("replay-mask.json", mask)
        # Keep the same production computation paths and wanted columns as 165. No materialized
        # feature cache, reduced history window, fake feature matrix or final target odds.
        with recorder.phase("build_legacy_138", audit):
            legacy = assemble_feature_matrix(
                clean_frames,
                start_date=day,
                end_date=day,
                wanted=frozenset(models.input_columns("legacy")),
                target_race_ids=frozenset([TARGET]),
                use_materialized=False,
            )
        with recorder.phase("build_mev_77_and_bind_inputs", audit):
            history = clean_frames.race_horses[
                ["race_id", "horse_id", "entry_status"]
            ].merge(
                clean_frames.races[["race_id", "race_date"]],
                on="race_id",
                validate="many_to_one",
            )
            features = build_from_snapshot(
                legacy,
                clean_raw,
                history,
                quote,
                race_id=TARGET,
                race_date=day.isoformat(),
            )
        with recorder.phase("production_source_fingerprint_and_feature_save", audit):
            features["source_evidence"].update(
                current_replay_source_fingerprint=source_fingerprint(
                    clean_frames, through=day
                ),
                snapshot_proof=proof,
                current_replay_mask_sha256=c.digest(mask),
                snapshot_acquired_by_at=source["acquired_by_at"],
                historical_asof_reconstruction=False,
            )
            features.update(
                mode=MODE,
                origin=ORIGIN,
                generated_at=recorder.clock.now(),
                evidence_admissible=False,
                can_adopt_model=False,
            )
            ready = recorder.save("features.json", features)
    return quote, features, ready


def _predict(features, kind, recorder, audit, *, suffix=""):
    name = "prediction_" + kind + suffix
    with recorder.phase(name, audit):
        available = recorder.clock.now()
        start = recorder.clock.now()
        kwargs = dict(race_id=TARGET, race_date="2026-10-25")
        if kind == "legacy":
            kwargs["history"] = features["legacy_history"]
        result = (models.infer_legacy if kind == "legacy" else models.infer_mev)(
            features["rows"][kind], **kwargs
        )
        computed = recorder.clock.now()
        payload = dict(
            mode=MODE,
            origin=ORIGIN,
            evidence_admissible=False,
            input_available_at=available,
            started_at=start,
            computed_at=computed,
            feature_sha256=c.digest(features),
            result=result,
        )
        ready = recorder.save(name + ".json", payload)
        return result, ready


def run(directory, *, process_started_ns=None, warm=True):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    clock = RealClock()
    recorder = Recorder(root, clock)
    started_ns = time.perf_counter_ns()
    process_started_ns = (
        started_ns if process_started_ns is None else process_started_ns
    )
    protocol = dict(
        mode=MODE,
        origin=ORIGIN,
        target_race_id=TARGET,
        diagnostic_guard_targets=list(GUARD_TARGETS),
        saved_quote_id=SNAPSHOT_ID,
        registered_at=clock.now(),
        manual_only=True,
        new_collection=False,
        database_write=False,
        evidence_admissible=False,
        can_adopt_model=False,
        comparison_data_added=0,
        max_quote_age_seconds=1800,
        source_pins=source_pins(),
        model_protocol=c.make_protocol(),
        feature_pool_since="2007-01-01",
        feature_pool_through="2026-10-25",
        live_preparation_budget_seconds=360,
        live_guard_budget_seconds=5,
        live_decision_window_seconds=60,
        warm_repeat_inference_and_save_only=warm,
    )
    publish(root / "protocol.json", protocol)
    engine, audit = create_readonly_engine()
    adapter = ObservationAdapters(enabled=True, engine_factory=lambda: engine)
    status = dict(
        state="started",
        mode=MODE,
        origin=ORIGIN,
        new_collection=False,
        database_write=False,
        evidence_admissible=False,
        can_adopt_model=False,
        comparison_data_added=0,
    )
    core_start, core_end = None, None
    network = dict(new_collection=False, python_network_blocked=True, attempts=[])
    try:
        with database_only() as network:
            with recorder.phase("readonly_connection", audit):
                with readonly_snapshot(engine, audit) as (_, proof):
                    recorder.save("connection-proof.json", proof)
            for rid in GUARD_TARGETS:
                with recorder.phase("diagnostic_guard_" + rid, audit):
                    recorder.save(
                        "diagnostic/" + rid + ".json",
                        collect_guard(adapter, "before", rid, clock),
                    )
            core_start = time.perf_counter_ns()
            with recorder.phase("before_guard", audit):
                before = collect_guard(adapter, "before", TARGET, clock)
                recorder.save("before.json", before)
                field = [
                    {k: r[k] for k in ("horse_id", "horse_number")}
                    for r in before["observations"]["entries"]["rows"]
                    if r["entry_status"] == "started"
                ]
            # The unmodified actual 165 feature reader must reject current saved results.
            with recorder.phase("production_reader_rejection", audit):
                reader = DatabaseFeatureReader(
                    enabled=True, session_factory=lambda: Session(engine)
                )
                rejection = expect_rejection(
                    lambda: reader.capture(TARGET, "2026-10-25", {}, clock)
                )
                c.require(
                    rejection["reason"] == "target_results_already_present",
                    "unexpected_reader_failure",
                )
                recorder.save("production-reader-rejection.json", rejection)
            quote, features, feature_ready = _snapshot_inputs(
                engine, audit, recorder, field
            )
            predictions = {}
            ready_times = []
            for kind in ("legacy", "mev"):
                predictions[kind], ready = _predict(features, kind, recorder, audit)
                ready_times.append(ready["available_by_at"])
            with recorder.phase("after_guard", audit):
                recorder.save(
                    "after.json", collect_guard(adapter, "after", TARGET, clock)
                )
            with recorder.phase("final_guard", audit):
                final = collect_guard(adapter, "decision", TARGET, clock)
                recorder.save("final.json", final)
            with recorder.phase("admission_rejection_and_save", audit):
                admission = current_admission(
                    quote,
                    post_time=before["observations"]["race"]["rows"][0]["post_time"],
                    at=clock.now(),
                    ready_times=ready_times,
                )
                recorder.save("admission.json", admission)
                c.require(
                    admission["evidence_admissible"] is False,
                    "replay_cannot_be_admitted",
                )
            core_end = time.perf_counter_ns()
            if warm:
                for kind in ("legacy", "mev"):
                    again, _ = _predict(features, kind, recorder, audit, suffix="_warm")
                    c.require(again == predictions[kind], "warm_prediction_changed")
            with recorder.phase("official_db_rows_and_historical_replay", audit):
                with readonly_snapshot(engine, audit) as (conn, _):
                    payouts = encode(
                        [
                            dict(r)
                            for r in conn.execute(
                                text(PAYOUT_SQL), dict(race_id=TARGET)
                            )
                            .mappings()
                            .all()
                        ]
                    )
                recorder.save(
                    "current-db-official-payouts.json",
                    dict(
                        race_id=TARGET,
                        payouts=payouts,
                        current_replay_valued=False,
                        payout_yen=None,
                        profit_yen=None,
                        reason="current_replay_not_a_historical_decision",
                    ),
                )
                recorder.save(
                    "historical-official-replay.json", historical_official_replay()
                )
            c.require(not network["attempts"], "collection_was_attempted")
            status.update(
                state="completed_current_replay",
                can_adopt_model=False,
                historical_asof_reconstruction=False,
                current_replay_payout_yen=None,
            )
    except Exception as error:
        status.update(state="failed_runtime", **safe_error(error))
    finally:
        adapter.close()
        engine.dispose()
    recorder.save("network-boundary.json", network)
    finished_ns = time.perf_counter_ns()
    status.update(
        finished_at=clock.now(),
        elapsed_process_seconds=(finished_ns - process_started_ns) / 1e9,
        peak_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    )
    # On macOS ru_maxrss is bytes; on Linux it is KiB. This workspace runs on macOS.
    import sys

    if sys.platform != "darwin":
        status["peak_process_rss_bytes"] *= 1024
    core_seconds = (core_end - core_start) / 1e9 if core_end is not None else None
    timing = dict(
        process_start_perf_ns=process_started_ns,
        run_start_perf_ns=started_ns,
        finished_perf_ns=finished_ns,
        module_import_startup_seconds=(started_ns - process_started_ns) / 1e9,
        elapsed_process_seconds=status["elapsed_process_seconds"],
        current_replay_core_seconds=core_seconds,
        core_start_perf_ns=core_start,
        core_end_perf_ns=core_end,
        startup_to_core_finish_seconds=(
            (core_end - process_started_ns) / 1e9 if core_end is not None else None
        ),
        final_report_and_manifest_writes_included=False,
        filesystem_cache_controlled=False,
        full_history_loaded=all(
            any(
                r["name"] == label and r["status"] == "completed"
                for r in recorder.phases
            )
            for label in ("load_full_legacy_pool", "load_full_mev_pool")
        ),
        phases=recorder.phases,
        peak_process_rss_bytes=status["peak_process_rss_bytes"],
        within_preparation_budget_360_seconds=(
            (core_end - process_started_ns) / 1e9 <= 360
            if core_end is not None
            else None
        ),
        real_http_fetch_and_rate_limit_wait_measured=False,
        guard_and_saved_data_only_no_live_deadline_claim=True,
    )
    publish(root / "status.json", status)
    publish(root / "timing.json", timing)
    publish(root / "database-audit.json", audit)
    publish(
        root / "clock.json",
        dict(simulated_clock=False, observations=clock.observations),
    )
    publish(root / "runtime.json", runtime())
    publish(
        root / "manifest.json",
        dict(
            mode=MODE,
            origin=ORIGIN,
            files={
                str(p.relative_to(root)): c.sha_bytes(p.read_bytes())
                for p in sorted(root.rglob("*"))
                if p.is_file()
            },
        ),
    )
    return status


def verify(directory):
    from .verification import verify_packet

    return verify_packet(directory, expected_source_pins=source_pins())
