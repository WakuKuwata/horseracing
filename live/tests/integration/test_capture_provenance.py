from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from horseracing_db.models import ChaosSnapshot
from horseracing_scrape.fetch import FetchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from horseracing_live import chaos_capture, cli
from horseracing_live.chaos_capture import ChaosCaptureReport, capture_chaos

from tests._capture_support import (
    CAPTURED_AT,
    RACE_ID,
    artifact_with_horizon,
    odds_payload,
    seed_race,
)

pytestmark = pytest.mark.integration


class _PayloadDelegate:
    source = "fixture-fetch-source"

    def __init__(self, payload: str | None = None, *, fail: bool = False) -> None:
        self.payload = payload or odds_payload()
        self.fail = fail
        self.deadlines: list[float] = []

    def set_deadline(self, deadline: float) -> None:
        self.deadlines.append(deadline)

    def get(self, _url: str, *, use_cache: bool = True) -> str:
        assert use_cache is False
        if self.fail:
            raise FetchError("fixture fetch failed")
        return self.payload


def _patch_artifact(monkeypatch, *, maximum: int = 86_400):
    artifact = artifact_with_horizon(maximum=maximum)
    monkeypatch.setattr(
        chaos_capture,
        "load_current_chaos_artifact",
        lambda _target_date: artifact,
    )
    monkeypatch.setattr(
        cli,
        "load_current_chaos_artifact",
        lambda _target_date: artifact,
        raising=False,
    )
    return artifact


def _patch_fetcher(monkeypatch, delegate: _PayloadDelegate) -> list[dict]:
    calls: list[dict] = []

    def make_capture_fetcher(**kwargs):
        calls.append(dict(kwargs))
        return delegate

    monkeypatch.setattr(
        "horseracing_live.chaos_politeness.make_capture_fetcher",
        make_capture_fetcher,
    )
    return calls


def test_capture_persists_trigger_policy_and_fetch_source(
    session,
    spy_fetcher,
) -> None:
    seed_race(session)
    fetcher = spy_fetcher(odds_payload())

    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=fetcher,
        artifact=artifact_with_horizon(),
        capture_trigger="predict_manual",
        capture_policy_version="capture_policy_v1",
        deadline=float("inf"),
        clock=lambda: CAPTURED_AT,
    )
    snapshot = session.scalar(
        select(ChaosSnapshot).where(ChaosSnapshot.race_id == RACE_ID)
    )

    assert report.status == "captured"
    assert snapshot is not None
    assert snapshot.capture_trigger == "predict_manual"
    assert snapshot.capture_policy_version == "capture_policy_v1"
    assert snapshot.source == fetcher.source
    assert snapshot.source != snapshot.capture_trigger


def test_database_check_rejects_unknown_capture_trigger(
    session,
    spy_fetcher,
) -> None:
    seed_race(session)
    report = capture_chaos(
        session,
        race_id=RACE_ID,
        fetcher=spy_fetcher(odds_payload()),
        artifact=artifact_with_horizon(),
        capture_trigger="explicit_command",
        capture_policy_version="capture_policy_v1",
        deadline=float("inf"),
        clock=lambda: CAPTURED_AT,
    )
    snapshot = session.get(ChaosSnapshot, report.chaos_snapshot_id)
    assert snapshot is not None

    snapshot.capture_trigger = "unknown_trigger"
    with pytest.raises(IntegrityError):
        session.flush()


def test_cli_route_defaults_bind_trigger_and_thirty_second_deadline(
    session,
    database_url,
    monkeypatch,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    seed_race(
        session,
        race_date=now.date(),
        post_time=now + datetime.timedelta(hours=1),
    )
    session.commit()
    _patch_artifact(monkeypatch)
    delegate = _PayloadDelegate()
    _patch_fetcher(monkeypatch, delegate)
    capture_calls: list[dict] = []

    def fake_capture(_session, **kwargs):
        capture_calls.append(dict(kwargs))
        return ChaosCaptureReport(
            race_id=kwargs["race_id"],
            status="skipped",
            reason="already_captured",
        )

    monkeypatch.setattr(cli, "capture_chaos", fake_capture)
    monkeypatch.setattr(
        cli,
        "time",
        SimpleNamespace(monotonic=lambda: 100.0),
        raising=False,
    )

    assert (
        cli.main(
            [
                "capture-chaos",
                "--date",
                now.date().isoformat(),
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "capture-chaos",
                "--race-id",
                RACE_ID,
                "--database-url",
                database_url,
            ]
        )
        == 0
    )

    assert [call["capture_trigger"] for call in capture_calls] == [
        "daily_operational",
        "explicit_command",
    ]
    assert all(
        call["capture_policy_version"] == "capture_policy_v1"
        for call in capture_calls
    )
    assert [call["deadline"] for call in capture_calls] == [130.0, 130.0]
    assert delegate.deadlines == [130.0, 130.0]


def test_daily_outside_horizon_refusal_is_nonzero_but_override_runs(
    session,
    database_url,
    monkeypatch,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    target_date = (now + datetime.timedelta(days=2)).date()
    seed_race(
        session,
        race_date=target_date,
        post_time=now + datetime.timedelta(days=2, hours=1),
    )
    session.commit()
    _patch_artifact(monkeypatch, maximum=3_600)
    _patch_fetcher(monkeypatch, _PayloadDelegate())

    refused = cli.main(
        [
            "capture-chaos",
            "--date",
            target_date.isoformat(),
            "--database-url",
            database_url,
        ]
    )
    allowed = cli.main(
        [
            "capture-chaos",
            "--date",
            target_date.isoformat(),
            "--allow-outside-horizon",
            "--database-url",
            database_url,
        ]
    )

    assert refused != 0
    assert allowed == 0


def test_daily_unknown_post_times_do_not_trigger_date_wide_refusal(
    session,
    database_url,
    monkeypatch,
    capsys,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    seed_race(session, race_date=now.date(), post_time=None)
    session.commit()
    _patch_artifact(monkeypatch)
    _patch_fetcher(monkeypatch, _PayloadDelegate())

    result = cli.main(
        [
            "capture-chaos",
            "--date",
            now.date().isoformat(),
            "--database-url",
            database_url,
        ]
    )

    assert result == 0
    assert "post_time_unknown=1" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        ("already_started", "post_time_elapsed"),
        ("already_captured", "already_captured"),
        ("fetch_failed", "fetch_failed"),
    ],
)
def test_ordinary_skip_or_fetch_failure_exits_zero(
    session,
    database_url,
    monkeypatch,
    capsys,
    scenario,
    expected_reason,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    seed_race(
        session,
        race_date=now.date(),
        post_time=(
            now - datetime.timedelta(minutes=1)
            if scenario == "already_started"
            else now + datetime.timedelta(hours=1)
        ),
    )
    if scenario == "already_captured":
        session.add(
            ChaosSnapshot(
                race_id=RACE_ID,
                captured_at=now - datetime.timedelta(minutes=5),
                source="fixture-fetch-source",
                capture_trigger="explicit_command",
                capture_policy_version="capture_policy_v1",
                seconds_to_post=3_900,
                capture_strength="confirmatory",
                field=[
                    {
                        "horse_id": f"H{number:02d}",
                        "horse_number": number,
                        "popularity": number,
                        "odds": str(number + 1),
                    }
                    for number in range(1, 5)
                ],
                n=4,
                content_digest="fixture-existing-capture",
                status="active",
            )
        )
    session.commit()
    _patch_artifact(monkeypatch)
    _patch_fetcher(
        monkeypatch,
        _PayloadDelegate(fail=scenario == "fetch_failed"),
    )

    result = cli.main(
        [
            "capture-chaos",
            "--race-id",
            RACE_ID,
            "--database-url",
            database_url,
        ]
    )

    assert result == 0
    assert f"{expected_reason}=1" in capsys.readouterr().out


def test_json_is_rejected_for_date_route(
    session,
    database_url,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(
            [
                "capture-chaos",
                "--date",
                datetime.date.today().isoformat(),
                "--json",
                "--database-url",
                database_url,
            ]
        )

    assert exc_info.value.code == 2
