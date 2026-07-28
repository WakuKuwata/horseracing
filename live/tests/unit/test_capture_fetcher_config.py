"""Feature 086 capture-only fetcher configuration and CLI wiring."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from horseracing_scrape.cli import _make_fetcher
from horseracing_scrape.fetch import FetchError

from horseracing_live import cli
from horseracing_live.chaos_politeness import (
    RequestPoliteness,
    deadline_for,
    make_capture_fetcher,
)


class _NoopPolicy:
    def pre_request(self, _url: str) -> None:
        pass

    def record_refusal(self, _refusal, *, request_url: str | None = None) -> None:
        del request_url
        raise AssertionError("a generic non-200 response is not a cooldown")


class _Response:
    def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.charset_encoding = "utf-8"
        self.headers = headers or {}


class _CountingClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return _Response(200, "User-agent: *\nAllow: /\n")
        return _Response(500, "unavailable")

    def close(self) -> None:
        pass


class _MissingRaceSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def get(self, _model, _race_id: str):
        return None

    def rollback(self) -> None:
        self.rollbacks += 1


def test_deadline_mapping_is_keyed_by_trigger() -> None:
    assert deadline_for("predict_manual") == 10.0
    assert deadline_for("predict_auto") == 10.0
    assert deadline_for("daily_operational") == 30.0
    assert deadline_for("explicit_command") == 30.0
    with pytest.raises(ValueError, match="unsupported capture trigger"):
        deadline_for("race_id")


def test_capture_fetcher_has_exact_stage_timeouts_and_one_attempt() -> None:
    fetcher = make_capture_fetcher(policy=_NoopPolicy())
    try:
        timeout = fetcher.client.timeout
        assert timeout.connect == 3.0
        assert timeout.read == 5.0
        assert timeout.write == 5.0
        assert timeout.pool == 3.0
        assert fetcher.max_retries == 1
    finally:
        fetcher.close()


def test_scrape_factory_remains_at_twenty_seconds_and_three_attempts() -> None:
    fetcher = _make_fetcher(1.0, None)
    try:
        timeout = fetcher._client.timeout
        assert timeout.connect == 20.0
        assert timeout.read == 20.0
        assert timeout.write == 20.0
        assert timeout.pool == 20.0
        assert fetcher.max_retries == 3
    finally:
        fetcher._client.close()


def test_capture_fetcher_calls_target_once_for_non_200() -> None:
    client = _CountingClient()
    fetcher = make_capture_fetcher(
        policy=_NoopPolicy(),
        client=client,
    )
    try:
        with pytest.raises(FetchError):
            fetcher.get("https://race.netkeiba.com/odds", use_cache=False)
    finally:
        fetcher.close()

    assert client.calls == [
        "https://race.netkeiba.com/robots.txt",
        "https://race.netkeiba.com/odds",
    ]
    assert client.calls.count("https://race.netkeiba.com/odds") == 1


def test_capture_fetcher_has_no_duplicate_process_local_rate_limit_wait() -> None:
    sleeps: list[float] = []
    client = _CountingClient()
    fetcher = make_capture_fetcher(
        policy=_NoopPolicy(),
        client=client,
        sleep=sleeps.append,
        clock=lambda: 0.0,
        respect_robots=False,
    )
    try:
        for _ in range(2):
            with pytest.raises(FetchError):
                fetcher.get("https://race.netkeiba.com/odds", use_cache=False)
    finally:
        fetcher.close()

    assert sleeps == []


def test_live_capture_path_constructs_the_capture_factory(monkeypatch, capsys) -> None:
    calls = 0

    seen_database_urls: list[str | None] = []

    def factory(*, database_url):
        nonlocal calls
        calls += 1
        seen_database_urls.append(database_url)
        return SimpleNamespace()

    monkeypatch.setattr(
        "horseracing_live.chaos_politeness.make_capture_fetcher",
        factory,
    )
    session = _MissingRaceSession()
    args = SimpleNamespace(
        race_id="202607260101",
        date=None,
        min_seconds_to_post=0,
    )

    assert cli._cmd_capture_chaos(session, args) == 0

    assert calls == 1
    assert seen_database_urls == [None]
    assert session.rollbacks == 1
    assert "race_not_found=1" in capsys.readouterr().out


def test_request_politeness_is_exported_for_capture_integration() -> None:
    # T036 wires this concrete gate into capture_chaos; keep the public seam explicit.
    assert RequestPoliteness.__name__ == "RequestPoliteness"
