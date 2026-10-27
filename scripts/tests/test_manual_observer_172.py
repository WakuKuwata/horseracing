"""The official manual observe command must stop before database/collector/model execution."""

import json
import socket
import sys
from types import SimpleNamespace

import pytest

import scripts.manual_observer_172 as manual


def profile(tmp_path, interval=60, **endpoint_changes):
    # Build a private test profile; this does not read the actual private profile or connect.
    from sqlalchemy.engine import URL

    endpoint = dict(
        username="hr_manual_observer",
        host="127.0.0.1",
        port=15432,
        database="horseracing",
        password="fixture-only",
    )
    endpoint.update(endpoint_changes)
    url = URL.create("postgresql+psycopg", **endpoint)
    lines = dict(
        DATABASE_URL=url.render_as_string(hide_password=False),
        HORSERACING_OBSERVER_PROFILE_VERSION="169-v1",
        HORSERACING_ENDPOINT_CONFIRMED="true",
        HORSERACING_EXPECTED_DATABASE=endpoint["database"],
        HORSERACING_EXPECTED_ROLE=endpoint["username"],
        HORSERACING_NETKEIBA_MIN_INTERVAL_S=str(interval),
    )
    path = tmp_path / "private.env"
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()))
    path.chmod(0o600)
    return path


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("manual pause must not attempt socket connections")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def test_observe_stops_with_nonzero_status_before_observer_import_or_call(
    tmp_path, monkeypatch
):
    policy = manual.load_policy()
    saved = []
    monkeypatch.setattr(
        manual,
        "runtime_settings",
        lambda p, c: dict(
            component=c,
            source_interval_seconds=p["source_interval_seconds"],
            effective_intervals=dict(source=60),
        ),
    )
    monkeypatch.setattr(
        manual, "save_report", lambda path, settings: saved.append(settings)
    )
    # The gate inspects sys.modules for a fresh process; other test modules in the same
    # session import the observer CLI, so stage the fresh-process premise explicitly.
    monkeypatch.delitem(
        sys.modules, "scripts.roi_explore.manual_observation_165.cli", raising=False
    )
    code = manual.main(
        [
            "observe",
            "--profile",
            str(profile(tmp_path)),
            "--report",
            str(tmp_path / "report.json"),
        ]
    )
    assert code == 3
    assert saved[0]["state"] == "PAUSED_NOT_STARTED"
    assert saved[0]["database_access"] is False
    assert saved[0]["manual_observer_called"] is False
    assert saved[0]["manual_observer_imported"] is False
    assert saved[0]["source_slot_reserved"] is False
    assert "scripts.roi_explore.manual_observation_165.cli" not in sys.modules
    assert policy["source_interval_seconds"] == 60


@pytest.mark.parametrize("interval", [1, 59, 120])
def test_profile_interval_drift_fails_before_runtime_or_io(
    tmp_path, monkeypatch, interval
):
    monkeypatch.setattr(
        manual,
        "runtime_settings",
        lambda *a: pytest.fail("runtime must not be reached"),
    )
    with pytest.raises(ValueError, match="manual_profile_common_interval_mismatch"):
        manual.observe_paused(
            manual.load_policy(),
            profile(tmp_path, interval=interval),
            tmp_path / "report.json",
        )


@pytest.mark.parametrize(
    "changes",
    [dict(username="owner_role"), dict(database="other_database"), dict(port=5432)],
)
def test_wrong_target_fails_before_runtime_or_io(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(
        manual,
        "runtime_settings",
        lambda *a: pytest.fail("runtime must not be reached"),
    )
    with pytest.raises(ValueError, match="endpoint_changed"):
        manual.observe_paused(
            manual.load_policy(), profile(tmp_path, **changes), tmp_path / "report.json"
        )


def test_already_imported_old_observer_is_not_accepted_as_a_fresh_pause(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(
        sys.modules, "scripts.roi_explore.manual_observation_165.cli", SimpleNamespace()
    )
    monkeypatch.setattr(manual, "runtime_settings", lambda *a: {})
    with pytest.raises(ValueError, match="observer_imported_before_pause_gate"):
        manual.observe_paused(
            manual.load_policy(), profile(tmp_path), tmp_path / "report.json"
        )


def test_no_enable_collection_flag_is_available(tmp_path):
    with pytest.raises(SystemExit) as error:
        manual.main(
            [
                "observe",
                "--profile",
                str(profile(tmp_path)),
                "--report",
                str(tmp_path / "report.json"),
                "--enable-collection",
            ]
        )
    assert error.value.code == 2


def test_collection_mode_cannot_be_enabled_through_policy(tmp_path):
    value = json.loads(manual.CONFIG.read_text())
    value["collection_paused"] = False
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="collection_pause_required"):
        manual.main(
            [
                "observe",
                "--policy",
                str(path),
                "--profile",
                str(profile(tmp_path)),
                "--report",
                str(tmp_path / "report.json"),
            ]
        )
