from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from scripts.roi_explore.manual_observation_165 import control
from scripts.roi_explore.manual_observation_165.backends import (
    FixtureBackend,
    LiveBackend,
)
from scripts.roi_explore.manual_observation_165.cli import observe, rehearse
from scripts.roi_explore.manual_observation_165.session import (
    execute,
    verify,
    validate_bundle,
    event_data,
)
from scripts.roi_explore.model_connection_163 import contract as c
from scripts.roi_explore.model_connection_163.clock import RealClock
from scripts.roi_explore.model_connection_163.run import connection_disabled
from scripts.roi_explore.prospective_rehearsal_162.store import read


def make_fixture(tmp_path, scenario="normal"):
    clock = RealClock()
    post = (c.time(clock.now()) + dt.timedelta(seconds=595)).isoformat()
    path, sha = control.create_plan(
        tmp_path / "plan",
        race_id="202699990001",
        post_time=post,
        clock=clock,
        origin="fixture_rehearsal",
    )
    backend = FixtureBackend(read(path), scenario=scenario)
    return path, sha, backend, clock


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    root = tmp_path_factory.mktemp("manual-complete")
    path, sha, backend, clock = make_fixture(root)
    with connection_disabled():
        result = execute(path, sha, root / "session", backend=backend, clock=clock)
        verified = verify(root / "session")
    return root, path, sha, backend, result, verified


def test_full_observation_order_and_receipts(completed):
    root, _, _, backend, status, result = completed
    assert status["state"] == "sealed"
    assert backend.calls == [
        "guard:before",
        "quote",
        "features",
        "guard:after",
        "guard:decision",
        "close",
    ]
    assert (
        result["origin"] == "fixture_rehearsal"
        and result["evidence_admissible"] is False
    )
    index = read(root / "session/index.json")
    plan = read(root / "session/plan.json")
    times = {
        k: event_data(root / "session", ref, k, plan, index["session_id"])[1]
        for k, ref in index["events"].items()
    }
    decision = read(root / "session/decision.json")
    assert all(
        c.time(times[k]) <= c.time(decision["decision_at"])
        for k in ("prediction_legacy", "prediction_mev")
    )
    assert set(index["events"]) == {
        "before",
        "quote",
        "features",
        "prediction_legacy",
        "prediction_mev",
        "after",
        "final",
    }
    assert len(decision["arms"]) == 2 and decision["can_adopt_model"] is False


def test_fixture_cannot_be_promoted_even_by_enable(completed, tmp_path):
    _, path, sha, _, _, _ = completed
    with connection_disabled(), pytest.raises(ValueError, match="mode_or_origin"):
        observe(path, sha, tmp_path / "forbidden", enable_collection=True)
    assert not (tmp_path / "forbidden").exists()


def test_live_disabled_fails_before_backend_construction(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165 import cli

    clock = RealClock()
    post = (c.time(clock.now()) + dt.timedelta(minutes=20)).isoformat()
    path, sha = control.create_plan(
        tmp_path / "plan", race_id="202605040907", post_time=post, clock=clock
    )

    def forbidden():
        raise AssertionError("backend constructed")

    monkeypatch.setattr(cli, "LiveBackend", forbidden)
    with (
        connection_disabled(),
        pytest.raises(ValueError, match="not_explicitly_enabled"),
    ):
        cli.observe(path, sha, tmp_path / "never")
    assert not (tmp_path / "never").exists()


@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("field_changed", "field_changed"),
        ("result_appeared", "results_already_present"),
        ("feature_failure", "fixture_feature_failure"),
    ],
)
def test_failed_session_keeps_reason_not_quote_history(tmp_path, scenario, reason):
    path, sha, backend, clock = make_fixture(tmp_path, scenario)
    with connection_disabled():
        status = execute(path, sha, tmp_path / "session", backend=backend, clock=clock)
        result = verify(tmp_path / "session")
    assert status["state"] == "failed" and status["error"] == reason
    assert status["evidence_admissible"] is False
    assert result["status"] == "verified_failure"
    assert not (tmp_path / "session/accepted.json").exists()
    assert not list((tmp_path / "session/events").glob("quote*"))
    assert read(tmp_path / "session/dropped-staging.json")


def test_duplicate_output_is_not_a_second_quote(completed):
    root, path, sha, _, _, _ = completed
    backend = FixtureBackend(read(path))
    with pytest.raises(ValueError, match="output_already_exists"):
        execute(path, sha, root / "session", backend=backend)
    assert backend.calls == []


def test_untrusted_backend_cannot_enter_live(tmp_path):
    path, sha, _, clock = make_fixture(tmp_path)

    class Impostor:
        origin = "manual_live"

    with pytest.raises(ValueError, match="untrusted_backend_type"):
        execute(
            path,
            sha,
            tmp_path / "no",
            backend=Impostor(),
            enable_collection=True,
            clock=clock,
        )
    assert not (tmp_path / "no").exists()


def test_plan_sha_rejection_before_any_backend_operation(tmp_path):
    path, _, backend, clock = make_fixture(tmp_path)
    with pytest.raises(ValueError, match="plan_sha"):
        execute(path, "0" * 64, tmp_path / "no", backend=backend, clock=clock)
    assert backend.calls == []


def test_unsupported_jst_year_preflight_no_collection(tmp_path):
    with pytest.raises(ValueError, match="unsupported_model_year"):
        control.create_plan(
            tmp_path / "none",
            race_id="202699990001",
            post_time="2026-12-31T15:00:00Z",
            clock=RealClock(),
        )
    assert not (tmp_path / "none").exists()


def test_quote_age_upper_bound_and_both_ready_gate(completed):
    root, _, _, _, _, _ = completed
    plan = read(root / "session/plan.json")
    index = read(root / "session/index.json")
    decision = read(root / "session/decision.json")
    future = (c.time(decision["decision_at"]) + dt.timedelta(minutes=31)).isoformat()
    with pytest.raises(ValueError, match="quote_not_fresh"):
        validate_bundle(root / "session", index, plan, future)
    # Missing one forecast cannot become a one-arm comparison.
    incomplete = copy.deepcopy(index)
    incomplete["events"].pop("prediction_mev")
    with pytest.raises(ValueError, match="bundle_incomplete"):
        validate_bundle(root / "session", incomplete, plan, decision["decision_at"])


def test_clock_entries_cover_both_model_computation_and_saving(completed):
    root, _, _, _, _, _ = completed
    index = read(root / "session/index.json")
    plan = read(root / "session/plan.json")
    stamps = {r["at"] for r in read(root / "session/clock.json")["observations"]}
    for kind in ("legacy", "mev"):
        prediction, ready = event_data(
            root / "session",
            index["events"]["prediction_" + kind],
            "prediction_" + kind,
            plan,
            index["session_id"],
        )
        assert {prediction["started_at"], prediction["computed_at"], ready} <= stamps


def test_injected_live_adapter_cannot_use_fixture_plan(tmp_path):
    path, sha, _, clock = make_fixture(tmp_path)
    backend = LiveBackend()
    with connection_disabled(), pytest.raises(ValueError, match="mode_or_origin"):
        execute(
            path,
            sha,
            tmp_path / "no",
            backend=backend,
            enable_collection=True,
            clock=clock,
        )
    assert backend.adapters._owned_engine is None and backend.adapters._fetcher is None


def test_rehearsal_public_command_writes_no_live_registry(tmp_path):
    with connection_disabled():
        result = rehearse(tmp_path / "rehearsal")
    assert result["origin"] == "fixture_rehearsal" and not result["evidence_admissible"]
    assert result["new_collection"] is result["database_access"] is False
    assert (tmp_path / "rehearsal/session/registry/202699990001.json").exists()


def test_nonempty_history_is_exactly_bound_to_features(completed, tmp_path):
    # Move a sealed bundle to an isolated directory; the original remains immutable.
    import shutil

    root, _, _, _, _, _ = completed
    target = tmp_path / "copy"
    shutil.copytree(root / "session", target)
    index = read(target / "index.json")
    plan = read(target / "plan.json")
    ref = index["events"]["features"]
    value = read(target / ref["path"])
    value["payload"]["legacy_history"] = [
        dict(
            race_id="202605040101",
            horse_id="fixture-1",
            race_date="2026-09-01",
            entry_status="started",
        )
    ]
    (target / ref["path"]).write_bytes(c.canonical(value))
    ref["sha256"] = c.digest(value)
    receipt = read(target / ref["receipt_path"])
    receipt["source_sha256"] = ref["sha256"]
    (target / ref["receipt_path"]).write_bytes(c.canonical(receipt))
    ref["receipt_sha256"] = c.digest(receipt)
    with pytest.raises(ValueError):
        validate_bundle(
            target, index, plan, read(target / "decision.json")["decision_at"]
        )


def test_manifest_tamper_stops_reader(completed, tmp_path):
    import shutil

    root, _, _, _, _, _ = completed
    target = tmp_path / "copy"
    shutil.copytree(root / "session", target)
    value = read(target / "status.json")
    value["evidence_admissible"] = True
    (target / "status.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="session_file_changed"):
        verify(target)


def test_cleanup_error_preserves_durable_session(tmp_path, monkeypatch):
    path, sha, backend, clock = make_fixture(tmp_path)

    def broken_close(self):
        raise OSError("close failed")

    monkeypatch.setattr(FixtureBackend, "close", broken_close)
    with connection_disabled():
        status = execute(path, sha, tmp_path / "session", backend=backend, clock=clock)
        result = verify(tmp_path / "session")
    assert status["state"] == "sealed" and status["cleanup_error"] == "OSError"
    assert result["status"] == "verified"


def test_prediction_ready_write_failure_is_not_admitted(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165 import session

    path, sha, backend, clock = make_fixture(tmp_path)
    original = session.publish

    def fail(path, value):
        if path.name == "prediction_legacy-ready.json":
            raise OSError("disk_full")
        return original(path, value)

    monkeypatch.setattr(session, "publish", fail)
    with connection_disabled():
        status = execute(path, sha, tmp_path / "session", backend=backend, clock=clock)
        result = verify(tmp_path / "session")
    assert status["state"] == "failed" and status["failed_step"] == "prediction_legacy"
    assert result["status"] == "verified_failure"
    assert not list((tmp_path / "session/events").glob("prediction*"))
    assert not (tmp_path / "session/accepted.json").exists()


def test_interrupted_staging_can_only_be_aborted(tmp_path, monkeypatch):
    from scripts.roi_explore.manual_observation_165.session import abort

    path, sha, backend, clock = make_fixture(tmp_path)

    def interrupt(self, *args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(FixtureBackend, "features", interrupt)
    with connection_disabled():
        with pytest.raises(KeyboardInterrupt):
            execute(path, sha, tmp_path / "session", backend=backend, clock=clock)
        result = abort(tmp_path / "session")
        again = abort(tmp_path / "session")
    assert result == again and result["status"] == "verified_failure"
    assert not list((tmp_path / "session/events").glob("quote*"))
    assert read(tmp_path / "session/clock.json")["scope"] == "abort_only"
    assert (
        read(tmp_path / "session/abort-receipt.json")["original_clock_log_present"]
        is False
    )


def test_accepted_session_cannot_be_aborted(completed):
    from scripts.roi_explore.manual_observation_165.session import abort

    with pytest.raises(ValueError, match="accepted_session_cannot_abort"):
        abort(completed[0] / "session")


def test_wait_is_bounded_and_deadline_fails_before_next_step():
    from scripts.roi_explore.manual_observation_165.session import (
        _wait_for_window,
        _deadline,
    )

    class Clock:
        def __init__(self):
            self.value = dt.datetime(2026, 10, 27, tzinfo=dt.UTC)

        def now(self):
            return self.value.isoformat()

    clock = Clock()
    post = clock.value + dt.timedelta(seconds=601)
    sleeps = []

    def advance(seconds):
        sleeps.append(seconds)
        clock.value += dt.timedelta(seconds=seconds)

    plan = dict(post_time=post.isoformat())
    _wait_for_window(plan, clock, advance)
    assert sleeps == [0.5, 0.5]
    assert clock.value == post - dt.timedelta(seconds=600)
    clock.value = post - dt.timedelta(seconds=540)
    with pytest.raises(ValueError, match="observation_deadline_elapsed"):
        _deadline(plan, clock)
