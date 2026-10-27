"""Independent attacks on the manual-only acquisition boundary; all I/O is fake."""

from __future__ import annotations

import datetime as dt

import pytest

from scripts.roi_explore.manual_observation_165.adapters import (
    ObservationAdapters,
    ObservationDisabled,
)

RACE = "202605040907"


class Clock:
    def __init__(self):
        self.value = dt.datetime(2026, 10, 27, 1, 0, tzinfo=dt.UTC)

    def now(self):
        result = self.value.isoformat()
        self.value += dt.timedelta(microseconds=1)
        return result


class Context:
    def __init__(self, value=None):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        return False


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Connection(Context):
    def __init__(self, engine, identity):
        super().__init__(self)
        self.engine, self.identity = engine, identity

    def execution_options(self, **values):
        assert values == {"isolation_level": "REPEATABLE READ"}
        self.engine.events.append((self.identity, "isolation"))
        return self

    def begin(self):
        self.engine.events.append((self.identity, "begin"))
        return Context()

    def execute(self, sql, parameters=None):
        text = str(sql)
        self.engine.events.append((self.identity, text))
        if text == "SET TRANSACTION READ ONLY":
            return Result([])
        assert parameters == {"race_id": RACE}
        if "FROM races " in text:
            return Result(
                [
                    dict(
                        race_id=RACE,
                        race_date=dt.date(2026, 10, 27),
                        post_time=dt.datetime(2026, 10, 27, 1, 10, tzinfo=dt.UTC),
                    )
                ]
            )
        if "FROM race_horses " in text:
            return Result(
                [
                    dict(horse_id="a", horse_number=1, entry_status="started"),
                    dict(horse_id="b", horse_number=2, entry_status="started"),
                ]
            )
        if "FROM race_results " in text:
            return Result([])
        raise AssertionError(text)


class Engine:
    def __init__(self):
        self.events = []
        self.connections = 0

    def connect(self):
        self.connections += 1
        return Connection(self, self.connections)

    def dispose(self):
        self.events.append(("dispose",))


@pytest.mark.parametrize("operation", ["engine", "guard", "quote"])
def test_disabled_observer_cannot_construct_any_io(operation):
    calls = []
    observer = ObservationAdapters(
        engine_factory=lambda: calls.append("db"),
        fetcher_factory=lambda: calls.append("http"),
    )
    with pytest.raises(ObservationDisabled):
        if operation == "engine":
            observer.engine()
        elif operation == "guard":
            observer.observe_guard("before", RACE, Clock())
        else:
            observer.fetch_quote(RACE, Clock())
    assert calls == []


def test_all_three_guards_get_distinct_readonly_snapshots():
    engine = Engine()
    calls = []
    observer = ObservationAdapters(
        enabled=True, engine_factory=lambda: (calls.append("factory"), engine)[1]
    )
    clock = Clock()
    observations = [
        observer.observe_guard(stage, RACE, clock)
        for stage in ("before", "after", "decision")
    ]
    assert calls == ["factory"]
    assert engine.connections == 3
    assert all(set(record) == {"race", "entries", "results"} for record in observations)
    for identity in range(1, 4):
        commands = [event[1] for event in engine.events if event[0] == identity]
        assert commands[:3] == ["isolation", "begin", "SET TRANSACTION READ ONLY"]
        assert sum("SELECT" in value for value in commands) == 3
        assert not any(
            value.startswith(("INSERT", "UPDATE", "DELETE")) for value in commands
        )
    assert (
        observations[0]["race"]["started_at"]
        < observations[1]["race"]["started_at"]
        < observations[2]["race"]["started_at"]
    )
    # Snapshot lower bound is shared by all queries of one snapshot, never the prior stage.
    for observation in observations:
        assert len({record["started_at"] for record in observation.values()}) == 1
        assert all(
            record["started_at"] <= record["completed_at"]
            for record in observation.values()
        )


def test_fetch_uses_uncached_original_response_and_real_interval():
    class Fetcher:
        def get(self, url, *, use_cache):
            assert use_cache is False
            assert url.endswith(f"race_id={RACE}&type=1&action=update")
            return '{"raw":"unchanged"}\r\n'

    observer = ObservationAdapters(enabled=True, fetcher_factory=Fetcher)
    result = observer.fetch_quote(RACE, Clock())
    assert result["raw_text"] == '{"raw":"unchanged"}\r\n'
    assert result["fetch_started_at"] < result["received_at"]


def test_naive_database_post_is_rejected():
    engine = Engine()
    original = Connection.execute

    def wrong(self, sql, parameters=None):
        result = original(self, sql, parameters)
        if "FROM races " in str(sql):
            result.rows[0]["post_time"] = dt.datetime(2026, 10, 27, 1, 10)
        return result

    # Fake DB only; no real database is reachable.
    from unittest.mock import patch

    with patch.object(Connection, "execute", wrong):
        with pytest.raises(ValueError, match="database_timestamp_naive"):
            ObservationAdapters(
                enabled=True, engine_factory=lambda: engine
            ).observe_guard("before", RACE, Clock())


def _registered_plan(tmp_path, monkeypatch, *, origin="manual_live"):
    from scripts.roi_explore.manual_observation_165 import control

    monkeypatch.setattr(control, "source_pins", lambda: {"fixed.py": "a" * 64})
    clock = Clock()
    path, checksum = control.create_plan(
        tmp_path / "plan",
        race_id=RACE,
        post_time="2026-10-27T01:20:00+00:00",
        clock=clock,
        origin=origin,
    )
    return path, checksum


def _preflight_clock():
    clock = Clock()
    clock.value = dt.datetime(2026, 10, 27, 1, 5, tzinfo=dt.UTC)
    return clock


def test_manual_plan_requires_explicit_enable_before_any_acquisition(
    tmp_path, monkeypatch
):
    from scripts.roi_explore.manual_observation_165.control import preflight

    path, checksum = _registered_plan(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="collection_not_explicitly_enabled"):
        preflight(
            path,
            checksum,
            enable_collection=False,
            origin="manual_live",
            clock=_preflight_clock(),
        )
    assert (
        preflight(
            path,
            checksum,
            enable_collection=True,
            origin="manual_live",
            clock=_preflight_clock(),
        )["origin"]
        == "manual_live"
    )


def test_fixture_plan_cannot_be_promoted_by_enable_flag(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165.control import preflight

    path, checksum = _registered_plan(tmp_path, monkeypatch, origin="fixture_rehearsal")
    with pytest.raises(ValueError, match="fixture_cannot_enable_collection"):
        preflight(
            path,
            checksum,
            enable_collection=True,
            origin="fixture_rehearsal",
            clock=_preflight_clock(),
        )
    with pytest.raises(ValueError, match="plan_mode_or_origin_mismatch"):
        preflight(
            path,
            checksum,
            enable_collection=True,
            origin="manual_live",
            clock=_preflight_clock(),
        )


def test_registration_receipt_cannot_appear_after_preparation_start(
    tmp_path, monkeypatch
):
    import json
    from scripts.roi_explore.manual_observation_165.control import preflight

    path, checksum = _registered_plan(tmp_path, monkeypatch)
    receipt_path = path.with_name("registration-ready.json")
    receipt = json.loads(receipt_path.read_text())
    receipt["available_by_at"] = "2026-10-27T01:05:00.000001+00:00"
    receipt_path.write_text(json.dumps(receipt))
    clock = _preflight_clock()
    clock.value += dt.timedelta(seconds=1)
    with pytest.raises(ValueError, match="plan_registered_too_late"):
        preflight(
            path, checksum, enable_collection=True, origin="manual_live", clock=clock
        )


def test_preregistered_source_code_drift_rejects_preflight(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165 import control

    path, checksum = _registered_plan(tmp_path, monkeypatch)
    monkeypatch.setattr(control, "source_pins", lambda: {"fixed.py": "b" * 64})
    with pytest.raises(ValueError, match="pinned_code_or_models_changed"):
        control.preflight(
            path,
            checksum,
            enable_collection=True,
            origin="manual_live",
            clock=_preflight_clock(),
        )


def test_expired_or_early_preparation_never_starts_observation(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165.control import preflight

    path, checksum = _registered_plan(tmp_path, monkeypatch)
    early = Clock()
    early.value = dt.datetime(2026, 10, 27, 1, 1, tzinfo=dt.UTC)
    with pytest.raises(ValueError, match="outside_preparation_window"):
        preflight(
            path, checksum, enable_collection=True, origin="manual_live", clock=early
        )
    clock = Clock()
    clock.value = dt.datetime(2026, 10, 27, 1, 11, tzinfo=dt.UTC)
    with pytest.raises(ValueError, match="observation_deadline_elapsed"):
        preflight(
            path, checksum, enable_collection=True, origin="manual_live", clock=clock
        )


def test_same_post_instant_with_different_iso_representation_is_accepted():
    from scripts.roi_explore.manual_observation_165.session import _field_guard

    plan = dict(
        race_id=RACE, race_date="2026-10-27", post_time="2026-10-27T10:10:00+09:00"
    )
    observed = "2026-10-27T01:00:00.000000+00:00"
    queries = {
        kind: dict(started_at=observed, completed_at=observed, rows=rows)
        for kind, rows in (
            (
                "race",
                [
                    dict(
                        race_id=RACE,
                        race_date="2026-10-27",
                        post_time="2026-10-27T01:10:00.000000+00:00",
                    )
                ],
            ),
            (
                "entries",
                [
                    dict(horse_id="a", horse_number=1, entry_status="started"),
                    dict(horse_id="b", horse_number=2, entry_status="started"),
                ],
            ),
            ("results", []),
        )
    }
    assert len(_field_guard(queries, plan=plan, anchor=observed)) == 2


def test_cli_observe_without_enable_never_constructs_live_backend(
    tmp_path, monkeypatch
):
    from scripts.roi_explore.manual_observation_165 import cli

    path, checksum = _registered_plan(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "RealClock", _preflight_clock)
    monkeypatch.setattr(cli, "LiveBackend", lambda: calls.append("constructed"))
    with pytest.raises(ValueError, match="collection_not_explicitly_enabled"):
        cli.observe(path, checksum, tmp_path / "output", enable_collection=False)
    assert calls == [] and not (tmp_path / "output").exists()


def test_cli_observe_cannot_promote_a_fixture_plan(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165 import cli

    path, checksum = _registered_plan(tmp_path, monkeypatch, origin="fixture_rehearsal")
    calls = []
    monkeypatch.setattr(cli, "RealClock", _preflight_clock)
    monkeypatch.setattr(cli, "LiveBackend", lambda: calls.append("constructed"))
    with pytest.raises(ValueError, match="plan_mode_or_origin_mismatch"):
        cli.observe(path, checksum, tmp_path / "output", enable_collection=True)
    assert calls == [] and not (tmp_path / "output").exists()


def test_cli_wrong_plan_sha_never_constructs_live_backend(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165 import cli

    path, _ = _registered_plan(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(cli, "RealClock", _preflight_clock)
    monkeypatch.setattr(cli, "LiveBackend", lambda: calls.append("constructed"))
    with pytest.raises(ValueError, match="plan_sha_mismatch"):
        cli.observe(path, "b" * 64, tmp_path / "output", enable_collection=True)
    assert calls == []


@pytest.fixture(scope="module")
def sealed_independent_packet(tmp_path_factory):
    """Actual 21 local models with synthetic inputs; no reachable connections."""
    from scripts.roi_explore.manual_observation_165 import control, session
    from scripts.roi_explore.manual_observation_165.backends import FixtureBackend
    from scripts.roi_explore.model_connection_163.clock import RealClock
    from scripts.roi_explore.model_connection_163.run import connection_disabled
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    root = tmp_path_factory.mktemp("independent-manual-packet")
    clock = RealClock()
    post = (
        dt.datetime.fromisoformat(clock.now()) + dt.timedelta(seconds=595)
    ).isoformat()
    with connection_disabled():
        path, checksum = control.create_plan(
            root / "registration",
            race_id="202699990001",
            post_time=post,
            clock=clock,
            origin="fixture_rehearsal",
        )
        backend = FixtureBackend(read(path))
        result = session.execute(
            path, checksum, root / "packet", backend=backend, clock=clock
        )
    assert result["state"] == "sealed"
    return root / "packet"


def _isolated_packet(source, target):
    import shutil
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    shutil.copytree(source, target)
    return target, read(target / "index.json"), read(target / "plan.json")


def _replace_event(root, index, kind, *, payload_change=None, ready_change=None):
    from scripts.roi_explore.model_connection_163 import contract as c
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    ref = index["events"][kind]
    body = read(root / ref["path"])
    if payload_change is not None:
        payload_change(body["payload"])
    (root / ref["path"]).write_bytes(c.canonical(body))
    ref["sha256"] = c.digest(body)
    receipt = read(root / ref["receipt_path"])
    receipt["source_sha256"] = ref["sha256"]
    if ready_change is not None:
        ready_change(receipt)
    (root / ref["receipt_path"]).write_bytes(c.canonical(receipt))
    ref["receipt_sha256"] = c.digest(receipt)


@pytest.mark.parametrize("kind", ["legacy", "mev"])
def test_each_prediction_durable_receipt_must_precede_decision(
    sealed_independent_packet, tmp_path, kind
):
    from scripts.roi_explore.manual_observation_165.session import validate_bundle
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    root, index, plan = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    decision_at = read(root / "decision.json")["decision_at"]
    late = (
        dt.datetime.fromisoformat(decision_at) + dt.timedelta(microseconds=1)
    ).isoformat()
    _replace_event(
        root,
        index,
        "prediction_" + kind,
        ready_change=lambda receipt: receipt.update(available_by_at=late),
    )
    with pytest.raises(ValueError, match="prediction_not_saved_before_decision"):
        validate_bundle(root, index, plan, decision_at)


def test_final_snapshot_cannot_begin_before_both_prediction_receipts(
    sealed_independent_packet, tmp_path
):
    from scripts.roi_explore.manual_observation_165.session import (
        validate_bundle,
        event_data,
    )
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    root, index, plan = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    decision_at = read(root / "decision.json")["decision_at"]
    last_ready = max(
        dt.datetime.fromisoformat(
            event_data(root, index["events"][kind], kind, plan, index["session_id"])[1]
        )
        for kind in ("prediction_legacy", "prediction_mev")
    )
    early = (last_ready - dt.timedelta(microseconds=1)).isoformat()

    def change(payload):
        for query in payload.values():
            query["started_at"] = early

    _replace_event(root, index, "final", payload_change=change)
    with pytest.raises(ValueError, match="final_guard_before_both_ready"):
        validate_bundle(root, index, plan, decision_at)


def test_after_snapshot_cannot_begin_before_model_computation_finishes(
    sealed_independent_packet, tmp_path
):
    from scripts.roi_explore.manual_observation_165.session import (
        validate_bundle,
        event_data,
    )
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    root, index, plan = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    decision_at = read(root / "decision.json")["decision_at"]
    end = max(
        dt.datetime.fromisoformat(
            event_data(root, index["events"][kind], kind, plan, index["session_id"])[0][
                "computed_at"
            ]
        )
        for kind in ("prediction_legacy", "prediction_mev")
    )
    early = (end - dt.timedelta(microseconds=1)).isoformat()

    def change(payload):
        for query in payload.values():
            query["started_at"] = early

    _replace_event(root, index, "after", payload_change=change)
    with pytest.raises(ValueError, match="after_guard_before_prediction"):
        validate_bundle(root, index, plan, decision_at)


def test_final_guard_cannot_hide_result_rows(sealed_independent_packet, tmp_path):
    from scripts.roi_explore.manual_observation_165.session import validate_bundle
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    root, index, plan = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    decision_at = read(root / "decision.json")["decision_at"]
    _replace_event(
        root,
        index,
        "final",
        payload_change=lambda payload: payload["results"].update(
            rows=[{"race_id": plan["race_id"]}]
        ),
    )
    with pytest.raises(ValueError, match="results_already_present"):
        validate_bundle(root, index, plan, decision_at)


@pytest.mark.parametrize(
    "kind,key",
    [
        ("prediction_mev", "ready"),
        ("prediction_legacy", "ready"),
        ("prediction_legacy", "started_at"),
        ("prediction_mev", "computed_at"),
        ("quote", "received_at"),
        ("features", "generated_at"),
        ("final", "snapshot"),
    ],
)
def test_prediction_ready_must_have_real_clock_observation(
    sealed_independent_packet, tmp_path, kind, key
):
    from scripts.roi_explore.manual_observation_165.session import verify, event_data
    from scripts.roi_explore.model_connection_163 import contract as c
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    root, index, plan = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    payload, ready = event_data(
        root,
        index["events"][kind],
        kind,
        plan,
        index["session_id"],
    )
    missing = (
        ready
        if key == "ready"
        else payload["race"]["started_at"]
        if key == "snapshot"
        else payload[key]
    )
    log = read(root / "clock.json")
    log["observations"] = [r for r in log["observations"] if r["at"] != missing]
    assert len(log["observations"]) < len(read(root / "clock.json")["observations"])
    (root / "clock.json").write_bytes(c.canonical(log))
    manifest = read(root / "manifest.json")
    manifest["files"]["clock.json"] = c.digest(log)
    (root / "manifest.json").write_bytes(c.canonical(manifest))
    with pytest.raises(ValueError, match="clock_missing|clock_observation_missing"):
        verify(root, recompute=False)


def test_abort_refuses_session_with_active_writer(sealed_independent_packet, tmp_path):
    from scripts.roi_explore.manual_observation_165 import session

    root, _, _ = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    # Simulate an observer that has not reached accepted publication yet.
    (root / "accepted.json").unlink()
    (root / "manifest.json").unlink()
    before = {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    with session._session_lock(root):
        with pytest.raises(
            ValueError, match="active_session_cannot_abort|session_writer_active"
        ):
            session.abort(root)
    after = {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and p.name != ".writer.lock"
    }
    assert after == {
        name: value for name, value in before.items() if name != ".writer.lock"
    }


def test_abort_recovers_failure_after_status_but_before_manifest(tmp_path):
    from scripts.roi_explore.manual_observation_165 import control, session
    from scripts.roi_explore.manual_observation_165.backends import FixtureBackend
    from scripts.roi_explore.model_connection_163.clock import RealClock
    from scripts.roi_explore.model_connection_163.run import connection_disabled
    from scripts.roi_explore.prospective_rehearsal_162.store import read

    clock = RealClock()
    post = (
        dt.datetime.fromisoformat(clock.now()) + dt.timedelta(seconds=595)
    ).isoformat()
    with connection_disabled():
        path, checksum = control.create_plan(
            tmp_path / "registration",
            race_id="202699990001",
            post_time=post,
            clock=clock,
            origin="fixture_rehearsal",
        )
        root = tmp_path / "packet"
        result = session.execute(
            path,
            checksum,
            root,
            backend=FixtureBackend(read(path), scenario="feature_failure"),
            clock=clock,
        )
        assert result["state"] == "failed"
        preserved = {
            name: (root / name).read_bytes()
            for name in ("status.json", "clock.json", "runtime.json")
        }
        (root / "manifest.json").unlink()
        recovered = session.abort(root)
    assert recovered["status"] == "verified_failure"
    assert recovered["evidence_admissible"] is False
    assert all((root / name).read_bytes() == data for name, data in preserved.items())
    assert not (root / "accepted.json").exists()
    assert session.abort(root) == recovered


def test_abort_receipt_survives_interruption_and_retry_is_idempotent(
    sealed_independent_packet, tmp_path, monkeypatch
):
    from scripts.roi_explore.manual_observation_165 import session
    from scripts.roi_explore.model_connection_163 import contract as c
    from scripts.roi_explore.model_connection_163.run import connection_disabled

    root, _, _ = _isolated_packet(sealed_independent_packet, tmp_path / "packet")
    for name in ("accepted.json", "manifest.json", "status.json"):
        (root / name).unlink()
    original = session.publish

    def interrupt_after_receipt(path, value):
        result = original(path, value)
        if path.name == "abort-receipt.json":
            raise KeyboardInterrupt("test interruption after durable abort receipt")
        return result

    monkeypatch.setattr(session, "publish", interrupt_after_receipt)
    with connection_disabled(), pytest.raises(KeyboardInterrupt):
        session.abort(root)
    receipt_sha = c.sha_bytes((root / "abort-receipt.json").read_bytes())
    monkeypatch.setattr(session, "publish", original)
    with connection_disabled():
        recovered = session.abort(root)
        again = session.abort(root)
    assert recovered == again and recovered["status"] == "verified_failure"
    assert c.sha_bytes((root / "abort-receipt.json").read_bytes()) == receipt_sha
    assert not (root / "accepted.json").exists()
    assert not list((root / "events").glob("quote*"))
    assert not list((root / "events").glob("prediction*"))
