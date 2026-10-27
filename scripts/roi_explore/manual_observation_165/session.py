"""One manual race, one immutable observation bundle. No scheduling or bet execution."""

from __future__ import annotations

import datetime as dt
import contextlib
import fcntl
import re
import time as system_time
import uuid
from pathlib import Path

from scripts.roi_explore.model_connection_163 import contract as c, models
from scripts.roi_explore.model_connection_163.clock import (
    RealClock,
    verify_observations,
)
from scripts.roi_explore.model_connection_163.quotes import (
    parse_saved,
    freshness_seconds,
)
from scripts.roi_explore.model_connection_163.run import runtime
from scripts.roi_explore.prospective_rehearsal_162.store import publish, read
from .backends import FixtureBackend, LiveBackend
from .control import MODE, ROOT, preflight


def _field_guard(observations, *, plan, anchor, expected=None):
    c.keys(observations, ["race", "entries", "results"], "guard_queries")
    anchor = c.time(anchor)
    for query in observations.values():
        c.keys(query, ["started_at", "completed_at", "rows"], "guard_query")
        start, end = c.time(query["started_at"]), c.time(query["completed_at"])
        c.require(
            start <= end <= anchor and anchor - start <= dt.timedelta(seconds=5),
            "guard_stale_or_future",
        )
    races = observations["race"]["rows"]
    c.require(
        len(races) == 1
        and set(races[0]) == {"race_id", "race_date", "post_time"}
        and races[0]["race_id"] == plan["race_id"]
        and races[0]["race_date"] == plan["race_date"]
        and c.time(races[0]["post_time"]) == c.time(plan["post_time"]),
        "guard_race_or_post_changed",
    )
    c.require(anchor < c.time(plan["post_time"]), "guard_not_prestart")
    entries = observations["entries"]["rows"]
    c.require(
        all(
            set(r) == {"horse_id", "horse_number", "entry_status"}
            and r["entry_status"] in ("started", "cancelled", "excluded")
            for r in entries
        ),
        "entry_status_invalid",
    )
    c.field([{k: v for k, v in r.items() if k != "entry_status"} for r in entries])
    active = [
        {k: v for k, v in r.items() if k != "entry_status"}
        for r in entries
        if r["entry_status"] == "started"
    ]
    c.field(active)
    if expected is not None:
        c.require(c.field(active) == c.field(expected), "field_changed")
    c.require(observations["results"]["rows"] == [], "results_already_present")
    return active


def _feature_check(payload, quote, plan, available):
    c.require(
        set(payload["rows"]) == {"legacy", "mev"}, "both_feature_matrices_required"
    )
    generated, observed = (
        c.time(payload["generated_at"]),
        c.time(payload["observed_by_at"]),
    )
    c.require(
        c.time(quote["received_at"]) <= generated <= observed <= c.time(available),
        "feature_clock_order",
    )
    source = payload["source_evidence"]
    c.require(
        source["read_only"] == "on" and source["isolation"] == "repeatable read",
        "feature_snapshot_not_readonly",
    )
    c.require(
        c.time(source["snapshot_started_at"])
        <= c.time(source["acquisition_finished_at"])
        <= generated,
        "feature_snapshot_clock_order",
    )
    if source.get("identity_map"):
        c.require(
            c.time(source["identity_mapping_available_at"])
            <= c.time(source["acquisition_finished_at"]),
            "identity_mapping_from_future",
        )
    c.require(
        c.time(payload["history_available_at"]) <= generated, "history_not_available"
    )
    expected = c.field(quote["rows"])
    derived = c.market_features(quote["rows"])
    for kind, rows in payload["rows"].items():
        frame, _ = models._input_frame(rows, plan["race_id"], plan["race_date"], kind)
        c.require(c.field(rows) == expected, "feature_field_changed")
        c.require(
            set(payload["source_times"][kind]) == set(models.input_columns(kind)),
            "feature_source_clocks_missing",
        )
        for stamp in payload["source_times"][kind].values():
            c.require(c.time(stamp) <= generated, "future_feature_dependency")
        for row in rows:
            c.require(
                c.time(payload["row_available_at"][kind][row["horse_id"]]) <= observed,
                "future_feature_row",
            )
            if kind == "mev":
                for key, value in derived[row["horse_id"]].items():
                    if key in row["features"]:
                        c.require(
                            row["features"][key] is not None
                            and abs(float(row["features"][key]) - float(value))
                            <= 1e-12,
                            "mev_quote_feature_mismatch",
                        )
                        c.require(
                            c.time(payload["source_times"][kind][key])
                            >= c.time(quote["received_at"]),
                            "market_feature_before_quote",
                        )
        if kind == "legacy":
            models._history(frame, payload["legacy_history"], plan["race_date"])


class Journal:
    def __init__(self, root, plan, clock, session_id):
        self.root, self.plan, self.clock, self.session_id = (
            Path(root),
            plan,
            clock,
            session_id,
        )
        self.refs = {}

    def event(self, kind, payload):
        c.require(re.fullmatch("[a-z_]+", kind) is not None, "event_kind_invalid")
        body = dict(
            schema_version=1,
            mode=MODE,
            origin=self.plan["origin"],
            kind=kind,
            race_id=self.plan["race_id"],
            session_id=self.session_id,
            payload=payload,
        )
        relative = f"events/{kind}.json"
        checksum = publish(self.root / relative, body)
        receipt = dict(
            mode=MODE,
            origin=self.plan["origin"],
            session_id=self.session_id,
            kind=kind,
            source_sha256=checksum,
            available_by_at=self.clock.now(),
        )
        ready = f"events/{kind}-ready.json"
        ready_sha = publish(self.root / ready, receipt)
        ref = dict(
            path=relative, sha256=checksum, receipt_path=ready, receipt_sha256=ready_sha
        )
        self.refs[kind] = ref
        return receipt["available_by_at"]

    def data(self, kind):
        return event_data(self.root, self.refs[kind], kind, self.plan, self.session_id)


def event_data(root, ref, kind, plan, session_id):
    def get(name, checksum):
        relative = Path(name)
        c.require(
            not relative.is_absolute() and ".." not in relative.parts,
            "event_path_unsafe",
        )
        path = Path(root)
        for part in relative.parts:
            path /= part
            c.require(not path.is_symlink(), "event_path_unsafe")
        c.require(c.sha_bytes(path.read_bytes()) == checksum, "event_sha_mismatch")
        return read(path)

    body = get(ref["path"], ref["sha256"])
    ready = get(ref["receipt_path"], ref["receipt_sha256"])
    c.require(
        body
        == dict(
            schema_version=1,
            mode=MODE,
            origin=plan["origin"],
            kind=kind,
            race_id=plan["race_id"],
            session_id=session_id,
            payload=body["payload"],
        ),
        "event_identity_mismatch",
    )
    c.require(
        ready
        == dict(
            mode=MODE,
            origin=plan["origin"],
            session_id=session_id,
            kind=kind,
            source_sha256=ref["sha256"],
            available_by_at=ready["available_by_at"],
        ),
        "event_ready_mismatch",
    )
    return body["payload"], ready["available_by_at"]


def validate_bundle(root, index, plan, at):
    c.require(
        index["origin"] == plan["origin"] and index["mode"] == MODE,
        "bundle_origin_changed",
    )
    values = {
        k: event_data(root, ref, k, plan, index["session_id"])
        for k, ref in index["events"].items()
    }
    c.require(
        set(values)
        == {
            "before",
            "quote",
            "features",
            "prediction_legacy",
            "prediction_mev",
            "after",
            "final",
        },
        "bundle_incomplete",
    )
    quote_raw, quote_ready = values["quote"]
    field = _field_guard(
        values["before"][0], plan=plan, anchor=quote_raw["fetch_started_at"]
    )
    c.require(
        c.time(values["before"][1]) <= c.time(quote_raw["fetch_started_at"]),
        "before_guard_not_ready",
    )
    quote = parse_saved(
        quote_raw["raw_text"],
        race_id=plan["race_id"],
        expected_field=field,
        source_url=quote_raw["source_url"],
        fetch_started_at=quote_raw["fetch_started_at"],
        received_at=quote_raw["received_at"],
        source="saved_response" if plan["origin"] == "manual_live" else "local_fixture",
    )
    freshness_seconds(quote, at)
    c.require(
        c.time(quote["received_at"]) <= c.time(quote_ready) <= c.time(at),
        "quote_not_ready",
    )
    features, feature_ready = values["features"]
    _feature_check(features, quote, plan, feature_ready)
    if plan["origin"] == "manual_live":
        c.require(
            features["source_evidence"].get("origin") != "synthetic_fixture",
            "fixture_cannot_be_live",
        )
    predictions = {}
    for kind in ("legacy", "mev"):
        p, ready = values["prediction_" + kind]
        c.keys(
            p,
            [
                "run_id",
                "producer",
                "model_version",
                "model_sha256",
                "input_available_at",
                "started_at",
                "computed_at",
                "rows",
                "evidence",
                "feature_event_sha256",
            ],
            "prediction",
        )
        c.require(
            p["feature_event_sha256"] == index["events"]["features"]["sha256"],
            "prediction_feature_binding_changed",
        )
        c.require(
            isinstance(p["run_id"], str)
            and p["run_id"]
            and p["producer"] == "actual_model",
            "prediction_run_invalid",
        )
        protocol = plan["model_protocol"]
        c.require(
            p["model_version"] == protocol["model_versions"][kind]
            and p["model_sha256"] == protocol["model_shas"][kind],
            "prediction_model_changed",
        )
        c.require(
            c.time(feature_ready)
            <= c.time(p["input_available_at"])
            <= c.time(p["started_at"])
            <= c.time(p["computed_at"])
            <= c.time(ready)
            <= c.time(at),
            "prediction_not_saved_before_decision",
        )
        expected = {r["horse_id"]: r for r in features["rows"][kind]}
        c.require(c.field(p["rows"]) == c.field(field), "prediction_field_changed")
        for row in p["rows"]:
            c.require(
                row["features"] == expected[row["horse_id"]]["features"],
                "prediction_input_changed",
            )
            c.require(
                row["input_asof"] == features["observed_by_at"]
                and c.time(row["input_asof"]) <= c.time(p["input_available_at"])
                and c.time(p["started_at"])
                <= c.time(row["created_at"])
                <= c.time(row["updated_at"])
                <= c.time(p["computed_at"]),
                "prediction_row_future",
            )
        models.validate_evidence_binding(
            kind, p, race_id=plan["race_id"], race_date=plan["race_date"]
        )
        if kind == "legacy":
            history = p["evidence"]["started_history"]
            consumed = [
                dict(zip(history["columns"], row, strict=True))
                for row in history["rows"]
            ]

            def order(row):
                return row["horse_id"], row["race_date"], row["race_id"]

            c.require(
                c.digest(sorted(consumed, key=order))
                == c.digest(sorted(features["legacy_history"], key=order)),
                "prediction_history_changed",
            )
        predictions[kind] = p
    after, after_ready = values["after"]
    _field_guard(after, plan=plan, anchor=after_ready, expected=field)
    c.require(
        min(c.time(v["started_at"]) for v in after.values())
        >= max(c.time(p["computed_at"]) for p in predictions.values()),
        "after_guard_before_prediction",
    )
    final, final_ready = values["final"]
    _field_guard(final, plan=plan, anchor=at, expected=field)
    c.require(c.time(final_ready) <= c.time(at), "final_guard_not_ready")
    c.require(
        min(c.time(v["started_at"]) for v in final.values())
        >= max(
            c.time(after_ready),
            *(c.time(values["prediction_" + k][1]) for k in ("legacy", "mev")),
        ),
        "final_guard_before_both_ready",
    )
    post = c.time(plan["post_time"])
    now = c.time(at)
    c.require(
        post - dt.timedelta(seconds=600) <= now < post - dt.timedelta(seconds=540),
        "outside_decision_window",
    )
    return quote, features, predictions, field


def _decision(root, index, plan, at):
    quote, features, predictions, field = validate_bundle(root, index, plan, at)
    bundle = dict(
        race_id=plan["race_id"],
        race_date=plan["race_date"],
        post_time=plan["post_time"],
        field=field,
        quote=quote,
        predictions=predictions,
        session_id=index["session_id"],
    )
    result = c.decide(bundle, at, plan["model_protocol"])
    result.pop("synthetic_post", None)
    result.update(
        mode=MODE,
        origin=plan["origin"],
        evidence_admissible=plan["origin"] == "manual_live",
        can_adopt_model=False,
        eligible_for_prospective=plan["origin"] == "manual_live",
        input_bundle_sha256=c.digest(index),
        identity_map=features["source_evidence"].get("identity_map", []),
    )
    return result


def _wait_for_window(plan, clock, sleep):
    target = c.time(plan["post_time"]) - dt.timedelta(seconds=600)
    while True:
        now = c.time(clock.now())
        c.require(
            now < c.time(plan["post_time"]) - dt.timedelta(seconds=540),
            "decision_deadline_elapsed",
        )
        if now >= target:
            return
        sleep(min(0.5, (target - now).total_seconds()))


def _deadline(plan, clock):
    c.require(
        c.time(clock.now()) < c.time(plan["post_time"]) - dt.timedelta(seconds=540),
        "observation_deadline_elapsed",
    )


def execute(
    plan_path,
    plan_sha,
    output,
    *,
    backend,
    enable_collection=False,
    clock=None,
    sleep=system_time.sleep,
    registry=None,
):
    clock = RealClock() if clock is None else clock
    c.require(type(clock) is RealClock, "real_clock_required")
    origin = (
        "manual_live"
        if type(backend) is LiveBackend
        else "fixture_rehearsal"
        if type(backend) is FixtureBackend
        else None
    )
    c.require(origin is not None, "untrusted_backend_type")
    plan = preflight(
        plan_path,
        plan_sha,
        enable_collection=enable_collection,
        origin=origin,
        clock=clock,
    )
    # Cross-output reservation prevents a second accepted observation of the same registered race.
    root = Path(output)
    c.require(not root.exists(), "output_already_exists")
    live_registry = (
        ROOT / "artifacts/manual_observation_registry_165"
        if registry is None
        else Path(registry)
    )
    if origin != "manual_live":
        live_registry = root / "registry"
    reservation = live_registry / (plan["race_id"] + ".json")
    if reservation.exists():
        raise ValueError("race_already_reserved")
    session_id = uuid.uuid4().hex
    publish(
        reservation,
        dict(
            mode=MODE,
            origin=origin,
            race_id=plan["race_id"],
            plan_sha256=plan_sha,
            output=str(root.resolve()),
            session_id=session_id,
        ),
    )
    root.mkdir(parents=True, exist_ok=True)
    publish(root / "plan.json", plan)
    publish(
        root / "registration-ready.json",
        read(Path(plan_path).with_name("registration-ready.json")),
    )
    with _session_lock(root):
        return _record_session(
            root, plan, plan_sha, session_id, origin, clock, backend, sleep
        )


@contextlib.contextmanager
def _session_lock(root):
    with (Path(root) / ".writer.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("active_session_cannot_abort") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _record_session(root, plan, plan_sha, session_id, origin, clock, backend, sleep):
    journal = Journal(root, plan, clock, session_id)
    status = dict(
        mode=MODE,
        origin=origin,
        race_id=plan["race_id"],
        session_id=session_id,
        plan_sha256=plan_sha,
        state="started",
        evidence_admissible=False,
        can_adopt_model=False,
        started_at=clock.now(),
        error=None,
    )
    publish(root / "invocation.json", status)
    phase = "before_guard"
    try:
        _deadline(plan, clock)
        before = backend.guard("before", plan["race_id"], clock)
        before_ready = journal.event("before", before)
        field = _field_guard(before, plan=plan, anchor=before_ready)
        phase = "quote"
        _deadline(plan, clock)
        raw = backend.quote(plan["race_id"], clock)
        _field_guard(before, plan=plan, anchor=raw["fetch_started_at"], expected=field)
        quote_ready = journal.event("quote", raw)
        quote = parse_saved(
            raw["raw_text"],
            race_id=plan["race_id"],
            expected_field=field,
            source_url=raw["source_url"],
            fetch_started_at=raw["fetch_started_at"],
            received_at=raw["received_at"],
            source="saved_response" if origin == "manual_live" else "local_fixture",
        )
        freshness_seconds(quote, quote_ready)
        phase = "features"
        _deadline(plan, clock)
        features = backend.features(plan["race_id"], plan["race_date"], quote, clock)
        feature_ready = journal.event("features", features)
        _feature_check(features, quote, plan, feature_ready)
        for kind in ("legacy", "mev"):
            phase = "prediction_" + kind
            _deadline(plan, clock)
            freshness_seconds(quote, clock.now())
            available, start = clock.now(), clock.now()
            args = dict(race_id=plan["race_id"], race_date=plan["race_date"])
            if kind == "legacy":
                args["history"] = features["legacy_history"]
            result = (models.infer_legacy if kind == "legacy" else models.infer_mev)(
                features["rows"][kind], **args
            )
            computed = clock.now()
            p = dict(
                run_id=uuid.uuid4().hex,
                producer="actual_model",
                model_version=plan["model_protocol"]["model_versions"][kind],
                model_sha256=plan["model_protocol"]["model_shas"][kind],
                input_available_at=available,
                started_at=start,
                computed_at=computed,
                evidence=result["evidence"],
                feature_event_sha256=journal.refs["features"]["sha256"],
                rows=[
                    dict(
                        **r,
                        probability=result["probabilities"][r["horse_id"]],
                        input_asof=features["observed_by_at"],
                        created_at=start,
                        updated_at=computed,
                    )
                    for r in features["rows"][kind]
                ],
            )
            journal.event("prediction_" + kind, p)
        phase = "after_guard"
        _deadline(plan, clock)
        after = backend.guard("after", plan["race_id"], clock)
        after_ready = journal.event("after", after)
        _field_guard(after, plan=plan, anchor=after_ready, expected=field)
        _wait_for_window(plan, clock, sleep)
        phase = "final_guard"
        final = backend.guard("decision", plan["race_id"], clock)
        journal.event("final", final)
        index = dict(
            mode=MODE, origin=origin, session_id=session_id, events=journal.refs
        )
        publish(root / "index.json", index)
        phase = "decision_and_seal"
        decision_at = clock.now()
        decision = _decision(root, index, plan, decision_at)
        decision_sha = publish(root / "decision.json", decision)
        saved = clock.now()
        validate_bundle(root, index, plan, saved)
        seal = dict(
            mode=MODE,
            origin=origin,
            decision_sha256=decision_sha,
            observed_saved_at=saved,
        )
        seal_sha = publish(root / "seal.json", seal)
        acknowledged = clock.now()
        validate_bundle(root, index, plan, acknowledged)
        publish(
            root / "accepted.json",
            dict(
                mode=MODE,
                origin=origin,
                seal_sha256=seal_sha,
                acknowledged_at=acknowledged,
                evidence_admissible=origin == "manual_live",
                can_adopt_model=False,
            ),
        )
        status.update(state="sealed", evidence_admissible=origin == "manual_live")
    except Exception as error:
        reason = str(error)
        safe = (
            reason
            if re.fullmatch("[A-Za-z0-9_:.-]{1,160}", reason)
            else "observation_failed"
        )
        status.update(
            state="failed",
            error=safe,
            error_class=type(error).__name__,
            evidence_admissible=False,
            failed_step=phase,
        )
        if not (root / "accepted.json").exists():
            dropped = {
                str(p.relative_to(root)): c.sha_bytes(p.read_bytes())
                for p in (root / "events").glob("*.json")
                if p.name.startswith(("quote", "features", "prediction"))
            }
            publish(root / "dropped-staging.json", dropped)
            for name in dropped:
                (root / name).unlink()
            for name in ("decision.json", "seal.json"):
                (root / name).unlink(missing_ok=True)
    finally:
        try:
            backend.close()
        except Exception as error:
            # Resource cleanup cannot erase an already durable observation/failure ledger.
            status["cleanup_error"] = type(error).__name__
    status["finished_at"] = clock.now()
    publish(root / "status.json", status)
    publish(
        root / "clock.json",
        dict(simulated_clock=False, observations=clock.observations),
    )
    publish(root / "runtime.json", runtime())
    if status["state"] == "sealed":
        publish(
            root / "pending-settlement.json",
            dict(
                mode=MODE,
                origin=origin,
                status="pending",
                race_id=plan["race_id"],
                arms={a: dict(payout_yen=None, profit_yen=None) for a in c.ARMS},
            ),
        )
    publish(
        root / "manifest.json",
        dict(
            mode=MODE,
            origin=origin,
            files={
                str(p.relative_to(root)): c.sha_bytes(p.read_bytes())
                for p in sorted(root.rglob("*"))
                if p.is_file()
            },
        ),
    )
    return status


def verify(root, *, recompute=True):
    root = Path(root)
    manifest = read(root / "manifest.json")
    baseline = set(manifest["files"]) | {"manifest.json"}
    actual = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.relative_to(root).parts[0] != "settlements"
    }
    c.require(actual == baseline, "session_file_inventory_changed")
    for name, checksum in manifest["files"].items():
        c.require(
            c.sha_bytes((root / name).read_bytes()) == checksum,
            "session_file_changed:" + name,
        )
    plan, status = read(root / "plan.json"), read(root / "status.json")
    c.require(
        manifest["mode"] == status["mode"] == plan["mode"] == MODE
        and manifest["origin"] == status["origin"] == plan["origin"]
        and status["state"] in ("failed", "sealed"),
        "session_state_or_origin_changed",
    )
    c.require(c.digest(plan) == status["plan_sha256"], "plan_binding_changed")
    registration = read(root / "registration-ready.json")
    c.require(
        registration["plan_sha256"] == status["plan_sha256"]
        and registration["origin"] == plan["origin"]
        and c.time(plan["registered_at"])
        <= c.time(registration["available_by_at"])
        <= c.time(status["started_at"]),
        "plan_registration_changed",
    )
    if plan["origin"] == "manual_live":
        c.require(
            c.time(registration["available_by_at"])
            <= c.time(plan["post_time"]) - dt.timedelta(seconds=900)
            <= c.time(status["started_at"])
            < c.time(plan["post_time"]) - dt.timedelta(seconds=540),
            "live_registration_or_invocation_late",
        )
    for name, checksum in plan["source_pins"].items():
        c.require(
            c.sha_bytes((ROOT / name).read_bytes()) == checksum,
            "source_changed:" + name,
        )
    c.require(
        plan["model_protocol"] == c.make_protocol()
        and read(root / "runtime.json") == runtime(),
        "model_or_runtime_changed",
    )
    log = read(root / "clock.json")
    c.require(log["simulated_clock"] is False, "clock_label_changed")
    verify_observations(log["observations"])
    if status["state"] == "failed":
        c.require(
            status["evidence_admissible"] is False
            and not (root / "accepted.json").exists(),
            "failed_session_admitted",
        )
        return dict(
            status="verified_failure",
            error=status["error"],
            origin=plan["origin"],
            evidence_admissible=False,
        )
    index, decision, seal, accepted = [
        read(root / name)
        for name in ("index.json", "decision.json", "seal.json", "accepted.json")
    ]
    c.require(index["session_id"] == status["session_id"], "session_identity_changed")
    c.require(
        decision == _decision(root, index, plan, decision["decision_at"]),
        "decision_replay_changed",
    )
    c.require(
        seal["decision_sha256"] == c.digest(decision)
        and accepted["seal_sha256"] == c.digest(seal),
        "seal_changed",
    )
    c.require(
        c.time(decision["decision_at"])
        <= c.time(seal["observed_saved_at"])
        <= c.time(accepted["acknowledged_at"]),
        "seal_time_order",
    )
    for at in (
        decision["decision_at"],
        seal["observed_saved_at"],
        accepted["acknowledged_at"],
    ):
        validate_bundle(root, index, plan, at)
    admissible = plan["origin"] == "manual_live"
    c.require(
        accepted["evidence_admissible"]
        == status["evidence_admissible"]
        == decision["evidence_admissible"]
        == admissible
        and status["can_adopt_model"] is False,
        "origin_admission_changed",
    )
    stamps = {r["at"] for r in log["observations"]}
    required_stamps = {
        status["started_at"],
        status["finished_at"],
        decision["decision_at"],
        seal["observed_saved_at"],
        accepted["acknowledged_at"],
    }
    for kind, ref in index["events"].items():
        payload, ready = event_data(root, ref, kind, plan, index["session_id"])
        required_stamps.add(ready)
        if kind in ("before", "after", "final"):
            for query in payload.values():
                required_stamps.update((query["started_at"], query["completed_at"]))
        elif kind == "quote":
            required_stamps.update(
                (payload["fetch_started_at"], payload["received_at"])
            )
        elif kind == "features":
            required_stamps.update(
                payload[k]
                for k in (
                    "generated_at",
                    "observed_by_at",
                    "captured_at",
                    "history_available_at",
                )
            )
            source = payload["source_evidence"]
            required_stamps.update(
                source[k]
                for k in (
                    "snapshot_started_at",
                    "snapshot_observed_at",
                    "acquisition_finished_at",
                    "feature_build_finished_at",
                    "identity_mapping_available_at",
                )
                if k in source
            )
            for mapping in (payload["source_times"], payload["row_available_at"]):
                for values in mapping.values():
                    required_stamps.update(values.values())
        else:
            required_stamps.update(
                payload[k] for k in ("input_available_at", "started_at", "computed_at")
            )
            for row in payload["rows"]:
                required_stamps.update(
                    row[k] for k in ("input_asof", "created_at", "updated_at")
                )
    c.require(
        required_stamps <= stamps,
        "decision_clock_missing",
    )
    if recompute:
        features, _ = event_data(
            root, index["events"]["features"], "features", plan, index["session_id"]
        )
        for kind in ("legacy", "mev"):
            original, _ = event_data(
                root,
                index["events"]["prediction_" + kind],
                "prediction_" + kind,
                plan,
                index["session_id"],
            )
            args = dict(race_id=plan["race_id"], race_date=plan["race_date"])
            if kind == "legacy":
                args["history"] = features["legacy_history"]
            again = (models.infer_legacy if kind == "legacy" else models.infer_mev)(
                features["rows"][kind], **args
            )
            c.require(
                again["probabilities"]
                == {r["horse_id"]: r["probability"] for r in original["rows"]}
                and again["evidence"] == original["evidence"],
                "native_model_replay_changed",
            )
    return dict(
        status="verified",
        origin=plan["origin"],
        evidence_admissible=admissible,
        can_adopt_model=False,
    )


def abort(root):
    """Close an interrupted local session, retaining hashes/reason but no failed price series."""
    root = Path(root)
    with _session_lock(root):
        return _abort_locked(root)


def _abort_locked(root):
    c.require(not (root / "accepted.json").exists(), "accepted_session_cannot_abort")
    if (root / "manifest.json").exists():
        return verify(root, recompute=False)
    plan, invocation = read(root / "plan.json"), read(root / "invocation.json")
    c.require(
        c.digest(plan) == invocation["plan_sha256"]
        and invocation["origin"] == plan["origin"],
        "abort_session_identity_changed",
    )
    clock = RealClock()
    dropped = {
        str(p.relative_to(root)): c.sha_bytes(p.read_bytes())
        for p in (root / "events").glob("*.json")
        if p.name.startswith(("quote", "features", "prediction"))
    }
    if not (root / "abort-receipt.json").exists():
        publish(
            root / "abort-receipt.json",
            dict(
                mode=MODE,
                origin=plan["origin"],
                session_id=invocation["session_id"],
                aborted_at=clock.now(),
                original_clock_log_present=(root / "clock.json").exists(),
                dropped_sha256=dropped,
                clock_observations=clock.observations.copy(),
            ),
        )
    receipt = read(root / "abort-receipt.json")
    c.require(
        receipt["session_id"] == invocation["session_id"],
        "abort_receipt_identity_changed",
    )
    verify_observations(receipt["clock_observations"])
    dropped = receipt["dropped_sha256"]
    for name in dropped:
        path = Path(name)
        c.require(
            len(path.parts) == 2
            and path.parts[0] == "events"
            and path.name.startswith(("quote", "features", "prediction")),
            "abort_path_invalid",
        )
        if (root / name).exists():
            c.require(
                c.sha_bytes((root / name).read_bytes()) == dropped[name],
                "abort_staging_changed",
            )
            (root / name).unlink()
    for name in ("decision.json", "seal.json"):
        (root / name).unlink(missing_ok=True)
    status = dict(
        invocation,
        state="failed",
        error="interrupted_session_aborted",
        failed_step="manual_abort",
        evidence_admissible=False,
        can_adopt_model=False,
        finished_at=clock.now(),
    )
    if (root / "status.json").exists():
        prior = read(root / "status.json")
        c.require(
            prior["state"] == "failed"
            and prior["session_id"] == invocation["session_id"],
            "abort_existing_status_invalid",
        )
    else:
        publish(root / "status.json", status)
    if not (root / "clock.json").exists():
        publish(
            root / "clock.json",
            dict(
                simulated_clock=False,
                scope="abort_only",
                observations=clock.observations,
            ),
        )
    elif not (root / "recovery-clock.json").exists():
        publish(
            root / "recovery-clock.json",
            dict(
                simulated_clock=False,
                scope="abort_only",
                observations=clock.observations,
            ),
        )
    publish(root / "runtime.json", runtime())
    publish(
        root / "manifest.json",
        dict(
            mode=MODE,
            origin=plan["origin"],
            files={
                str(p.relative_to(root)): c.sha_bytes(p.read_bytes())
                for p in sorted(root.rglob("*"))
                if p.is_file()
            },
        ),
    )
    return verify(root, recompute=False)
