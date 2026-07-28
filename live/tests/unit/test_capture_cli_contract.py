"""Feature 086 capture-chaos CLI route defaults, JSON, horizon gate, and summaries."""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest

from horseracing_live import cli
from horseracing_live.chaos_capture import ChaosCaptureReport

RID = "202607260101"
DATE = datetime.date(2026, 7, 26)
NOW = datetime.datetime(2026, 7, 25, 12, tzinfo=datetime.UTC)


class _Session:
    def __init__(self, *, latest_post_time=None):
        self.latest_post_time = latest_post_time
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, _statement):
        return self.latest_post_time

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _Fetcher:
    def __init__(self):
        self.deadlines: list[float] = []
        self.closed = False

    def set_deadline(self, deadline):
        self.deadlines.append(deadline)

    def close(self):
        self.closed = True


def _args(**overrides):
    values = {
        "race_id": RID,
        "date": None,
        "min_seconds_to_post": 0,
        "trigger": None,
        "json": True,
        "capture_deadline_seconds": None,
        "allow_outside_horizon": False,
        "database_url": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _wire(monkeypatch, reports):
    fetcher = _Fetcher()
    seen: list[dict] = []
    iterator = iter(reports)

    def capture(_session, **kwargs):
        seen.append(kwargs)
        return next(iterator)

    monkeypatch.setattr(cli, "capture_chaos", capture)
    monkeypatch.setattr(
        "horseracing_live.chaos_politeness.make_capture_fetcher",
        lambda **_kwargs: fetcher,
    )
    return fetcher, seen


def test_race_route_defaults_to_explicit_command_and_thirty_seconds(
    monkeypatch,
    capsys,
):
    report = ChaosCaptureReport(RID, "skipped", "post_time_unknown")
    fetcher, seen = _wire(monkeypatch, [report])
    ticks = iter([100.0, 100.25])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))

    assert cli._cmd_capture_chaos(_Session(), _args()) == 0
    payload = json.loads(capsys.readouterr().out)

    assert seen[0]["capture_trigger"] == "explicit_command"
    assert seen[0]["deadline"] == 130.0
    assert fetcher.deadlines == [130.0]
    assert payload["elapsed_s"] == pytest.approx(0.25)


def test_date_route_defaults_to_daily_operational_and_thirty_seconds(
    monkeypatch,
    capsys,
):
    report = ChaosCaptureReport(RID, "skipped", "result_settled")
    fetcher, seen = _wire(monkeypatch, [report])
    monkeypatch.setattr(cli, "list_pending", lambda *_a, **_kw: [RID])
    ticks = iter([200.0, 200.1])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(ticks))

    args = _args(
        race_id=None,
        date=DATE,
        json=False,
        allow_outside_horizon=True,
    )
    assert cli._cmd_capture_chaos(_Session(), args) == 0
    output = capsys.readouterr().out

    assert seen[0]["capture_trigger"] == "daily_operational"
    assert seen[0]["deadline"] == 230.0
    assert fetcher.deadlines == [230.0]
    assert "skipped_eligible=1" in output


def test_daily_summary_splits_skips_and_unknown_defaults_unfetchable(
    monkeypatch,
    capsys,
):
    race_ids = [RID, "202607260102", "202607260103"]
    reports = [
        ChaosCaptureReport(race_ids[0], "skipped", "result_settled"),
        ChaosCaptureReport(race_ids[1], "skipped", "source_cooldown"),
        ChaosCaptureReport(race_ids[2], "skipped", "new_unknown_reason"),
    ]
    _wire(monkeypatch, reports)
    monkeypatch.setattr(cli, "list_pending", lambda *_a, **_kw: race_ids)

    args = _args(
        race_id=None,
        date=DATE,
        json=False,
        allow_outside_horizon=True,
    )
    assert cli._cmd_capture_chaos(_Session(), args) == 0
    output = capsys.readouterr().out

    assert "skipped_eligible=1" in output
    assert "skipped_unfetchable=2" in output


def test_daily_route_refuses_once_when_latest_post_is_beyond_horizon(
    monkeypatch,
    capsys,
):
    artifact = SimpleNamespace(
        preregistration={
            "primary_horizon": {"maximum_seconds_to_post": 100}
        }
    )
    monkeypatch.setattr(cli, "_now", lambda: NOW)
    monkeypatch.setattr(cli, "load_current_chaos_artifact", lambda _date: artifact)
    session = _Session(
        latest_post_time=NOW + datetime.timedelta(seconds=101)
    )
    args = _args(race_id=None, date=DATE, json=False)

    assert cli._cmd_capture_chaos(session, args) == 2
    assert "beyond the primary horizon" in capsys.readouterr().out


def test_no_known_post_time_does_not_refuse_daily_route(monkeypatch, capsys):
    report = ChaosCaptureReport(RID, "skipped", "post_time_unknown")
    _wire(monkeypatch, [report])
    monkeypatch.setattr(cli, "list_pending", lambda *_a, **_kw: [RID])
    args = _args(race_id=None, date=DATE, json=False)

    assert cli._cmd_capture_chaos(_Session(latest_post_time=None), args) == 0
    assert "skipped_eligible=1" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["capture-chaos", "--date", "2026-07-26", "--json"],
        [
            "capture-chaos",
            "--race-id",
            RID,
            "--allow-outside-horizon",
        ],
    ],
)
def test_route_only_flags_are_argparse_errors(argv):
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)

    assert caught.value.code == 2


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_deadline_must_be_positive_and_finite(monkeypatch, capsys, value):
    monkeypatch.setattr(
        "horseracing_live.chaos_politeness.make_capture_fetcher",
        lambda **_kwargs: pytest.fail("invalid deadline must fail before factory"),
    )
    args = _args(capture_deadline_seconds=float(value))

    assert cli._cmd_capture_chaos(_Session(), args) == 2
    assert "must be positive" in capsys.readouterr().out
