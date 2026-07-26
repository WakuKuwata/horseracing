"""Feature 084 capture-discipline guards (CAP-1..7, SNAP-2/3)."""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest

from horseracing_live import cli
from horseracing_live.chaos_capture import (
    ChaosCaptureRejected,
    ChaosCaptureReport,
    FrozenEntry,
    acquire_fresh_capture,
    build_frozen_field,
    decide_capture_strength,
)
from horseracing_scrape.parse.odds import parse_odds

_RACE_ID = "202607260101"
_CAPTURED_AT = datetime.datetime(2026, 7, 26, 5, 30, tzinfo=datetime.UTC)
_POST_TIME = datetime.datetime(2026, 7, 26, 6, 0, tzinfo=datetime.UTC)


class StubFetcher:
    def __init__(self, payload: str, *, source: str | None = "adapter-source"):
        self.payload = payload
        self.source = source
        self.calls: list[tuple[str, bool]] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.calls.append((url, use_cache))
        return self.payload


def _entries(n: int = 4) -> list[FrozenEntry]:
    return [FrozenEntry(horse_id=f"H{i:02d}", horse_number=i) for i in range(1, n + 1)]


def _payload(
    n: int = 4,
    *,
    ranks: list[int | None] | None = None,
    odds: list[str | None] | None = None,
    omit_number: int | None = None,
) -> str:
    ranks = ranks or list(range(1, n + 1))
    odds = odds or [str(1.5 + i) for i in range(n)]
    rows = {}
    for number, (rank, price) in enumerate(zip(ranks, odds, strict=True), start=1):
        if number == omit_number:
            continue
        rows[f"{number:02d}"] = [price, "0.0", rank]
    return json.dumps({"data": {"odds": {"1": rows}}})


def _pending_sequence(*values: bool):
    remaining = list(values)

    def check(_session, _race_id):
        value = remaining.pop(0)
        return value, "ok" if value else "settled"

    return check


@pytest.mark.parametrize(
    ("fresh_fetch", "pending_before", "pending_after", "post_time", "expected"),
    [
        (True, True, True, _POST_TIME, "confirmatory"),
        (True, True, True, None, "weak"),
        (True, True, None, _POST_TIME, "weak"),
        (True, None, True, _POST_TIME, "weak"),
        (False, True, True, _POST_TIME, "unknown"),
        (True, None, None, _POST_TIME, "unknown"),
        (True, True, True, _CAPTURED_AT, "unknown"),
    ],
)
def test_capture_strength_decision_table(
    fresh_fetch, pending_before, pending_after, post_time, expected
):
    assert (
        decide_capture_strength(
            fresh_fetch=fresh_fetch,
            pending_before=pending_before,
            pending_after=pending_after,
            post_time=post_time,
            captured_at=_CAPTURED_AT,
        )
        == expected
    )


def test_fresh_fetch_is_no_cache_and_source_comes_from_adapter():
    fetcher = StubFetcher(_payload(), source="actual-adapter")
    capture = acquire_fresh_capture(
        object(),
        race_id=_RACE_ID,
        entries=_entries(),
        post_time=_POST_TIME,
        fetcher=fetcher,
        clock=lambda: _CAPTURED_AT,
        pending_check=_pending_sequence(True, True),
    )

    assert fetcher.calls and fetcher.calls[0][1] is False
    assert capture.source == "actual-adapter"
    assert capture.capture_strength == "confirmatory"
    assert capture.seconds_to_post == 1800


def test_post_time_unknown_is_expected_weak_capture():
    capture = acquire_fresh_capture(
        object(),
        race_id=_RACE_ID,
        entries=_entries(),
        post_time=None,
        fetcher=StubFetcher(_payload()),
        clock=lambda: _CAPTURED_AT,
        pending_check=_pending_sequence(True, True),
    )
    assert capture.capture_strength == "weak"
    assert capture.seconds_to_post is None


def test_settled_before_fetch_never_calls_fetcher():
    fetcher = StubFetcher(_payload())
    with pytest.raises(ChaosCaptureRejected) as caught:
        acquire_fresh_capture(
            object(),
            race_id=_RACE_ID,
            entries=_entries(),
            post_time=_POST_TIME,
            fetcher=fetcher,
            clock=lambda: _CAPTURED_AT,
            pending_check=_pending_sequence(False),
        )
    assert caught.value.reason == "result_settled"
    assert fetcher.calls == []


def test_settled_during_fetch_is_caught_by_the_second_pending_check():
    fetcher = StubFetcher(_payload())
    with pytest.raises(ChaosCaptureRejected) as caught:
        acquire_fresh_capture(
            object(),
            race_id=_RACE_ID,
            entries=_entries(),
            post_time=_POST_TIME,
            fetcher=fetcher,
            clock=lambda: _CAPTURED_AT,
            pending_check=_pending_sequence(True, False),
        )
    assert caught.value.reason == "result_settled"
    assert fetcher.calls[0][1] is False


@pytest.mark.parametrize(
    ("post_time", "minimum", "reason"),
    [
        (_CAPTURED_AT, 0, "post_time_elapsed"),
        (_CAPTURED_AT - datetime.timedelta(seconds=1), 0, "post_time_elapsed"),
        (_CAPTURED_AT + datetime.timedelta(seconds=599), 600, "min_seconds_to_post"),
    ],
)
def test_post_time_gates(post_time, minimum, reason):
    with pytest.raises(ChaosCaptureRejected) as caught:
        acquire_fresh_capture(
            object(),
            race_id=_RACE_ID,
            entries=_entries(),
            post_time=post_time,
            fetcher=StubFetcher(_payload()),
            clock=lambda: _CAPTURED_AT,
            pending_check=_pending_sequence(True, True),
            min_seconds_to_post=minimum,
        )
    assert caught.value.reason == reason


def test_fetcher_without_source_is_rejected_not_caller_overridden():
    with pytest.raises(ChaosCaptureRejected) as caught:
        acquire_fresh_capture(
            object(),
            race_id=_RACE_ID,
            entries=_entries(),
            post_time=_POST_TIME,
            fetcher=StubFetcher(_payload(), source=None),
            clock=lambda: _CAPTURED_AT,
            pending_check=_pending_sequence(True, True),
        )
    assert caught.value.reason == "source_unavailable"


@pytest.mark.parametrize(
    ("entries", "payload", "reason"),
    [
        ([], _payload(), "no_started_horses"),
        (_entries(3), _payload(3), "field_too_small"),
        (_entries(), _payload(ranks=[1, 2, None, 4]), "invalid_popularity_ranks"),
        (_entries(), _payload(ranks=[1, 2, 2, 4]), "invalid_popularity_ranks"),
        (_entries(), _payload(omit_number=4), "partial_market_odds"),
        (_entries(), _payload(odds=["2.0", "3.0", "4.0", None]), "partial_market_odds"),
        (_entries(), _payload(odds=["2.0", "3.0", "4.0", "0"]), "partial_market_odds"),
    ],
)
def test_every_eligibility_rejection_reason(entries, payload, reason):
    scraped = parse_odds(payload, _RACE_ID)
    with pytest.raises(ChaosCaptureRejected) as caught:
        build_frozen_field(entries, scraped)
    assert caught.value.reason == reason


def test_popularity_gaps_are_allowed_and_never_reranked():
    scraped = parse_odds(_payload(ranks=[1, 2, 4, 5]), _RACE_ID)
    field = build_frozen_field(_entries(), scraped)
    assert [row["popularity"] for row in field] == [1, 2, 4, 5]


def test_capture_cli_isolates_one_bad_race(monkeypatch, capsys):
    race_ids = (_RACE_ID, "202607260102")

    class StubSession:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def get(self, _model, race_id):
            return SimpleNamespace(race_id=race_id, race_date=_CAPTURED_AT.date())

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    def capture(_session, *, race_id, **_kwargs):
        if race_id == race_ids[0]:
            raise RuntimeError("one race fails")
        return ChaosCaptureReport(
            race_id=race_id,
            status="captured",
            reason="ok",
            capture_strength="confirmatory",
        )

    monkeypatch.setattr(cli, "list_pending", lambda _session, *, date: list(race_ids))
    monkeypatch.setattr(cli, "load_current_chaos_artifact", lambda _date: object())
    monkeypatch.setattr(cli, "capture_chaos", capture)
    monkeypatch.setattr("horseracing_scrape.cli._make_fetcher", lambda *_args: object())
    session = StubSession()
    args = SimpleNamespace(
        race_id=None,
        date=_CAPTURED_AT.date(),
        min_seconds_to_post=0,
    )

    assert cli._cmd_capture_chaos(session, args) == 0
    output = capsys.readouterr().out
    assert "captured=1" in output
    assert "rejected=1" in output
    assert "error:RuntimeError=1" in output
    assert session.commits == 1
