"""Feature 086 cross-process request reservations and wall-clock fetch deadline."""

from __future__ import annotations

import datetime
import json
import multiprocessing
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from horseracing_db.models import FetchThrottleState
from horseracing_scrape.fetch import FetchRefused, _domain
from horseracing_scrape.urls import win_odds_url
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from horseracing_live.chaos_politeness import (
    PolitenessRefused,
    RequestPoliteness,
    deadline_for,
    make_capture_fetcher,
)
from horseracing_live import cli
from horseracing_live.chaos_capture import NetkeibaOddsFetcher, capture_chaos

from tests._capture_support import (
    CAPTURED_AT,
    CAPTURE_POLICY_VERSION,
    CAPTURE_TRIGGER,
    RACE_ID,
    artifact_with_horizon,
    seed_race,
)

pytestmark = pytest.mark.integration


class _RecordingHandler(BaseHTTPRequestHandler):
    server: "_RecordingServer"

    def do_GET(self) -> None:  # noqa: N802
        self.server.requests.append((self.path, time.monotonic()))
        delay = self.server.delays.get(self.path, 0.0)
        if delay:
            time.sleep(delay)
        body = (
            b"User-agent: *\nAllow: /\n"
            if self.path == "/robots.txt"
            else b'{"ok": true}'
        )
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Deadline tests deliberately cancel an in-flight response.
            pass

    def log_message(self, _format: str, *_args) -> None:
        pass


class _RecordingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, delays: dict[str, float] | None = None):
        super().__init__(("127.0.0.1", 0), _RecordingHandler)
        self.delays = delays or {}
        self.requests: list[tuple[str, float]] = []


@contextmanager
def _http_server(
    *,
    delays: dict[str, float] | None = None,
) -> Iterator[tuple[str, _RecordingServer]]:
    server = _RecordingServer(delays)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class _SpyClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        raise AssertionError("a cooldown installed while waiting must prevent the request")


class _RefusedResponse:
    def __init__(self, status_code: int, *, retry_after: str | None = None) -> None:
        self.status_code = status_code
        self.text = "refused"
        self.content = b"refused"
        self.charset_encoding = "utf-8"
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


class _RefusedClient:
    def __init__(self, response: _RefusedResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        return self.response

    def close(self) -> None:
        pass


def _reserve_in_process(
    database_url: str,
    url: str,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    from horseracing_db.session import create_db_engine

    engine = create_db_engine(database_url)
    try:
        gate = RequestPoliteness.for_engine(engine, min_interval_s=0.05)
        start.wait(timeout=10.0)
        decision = gate.reserve(url)
        results.put((decision.allowed, decision.reason))
    except Exception as exc:  # noqa: BLE001 - child must report failures to the parent
        results.put((False, f"{type(exc).__name__}: {exc}"))
    finally:
        engine.dispose()


def test_two_separate_fetchers_share_the_one_second_reservation(engine) -> None:
    with _http_server() as (base_url, server):
        url = f"{base_url}/odds"
        first = make_capture_fetcher(engine=engine, respect_robots=False)
        second = make_capture_fetcher(engine=engine, respect_robots=False)
        try:
            first.get(url, use_cache=False)
            second.get(url, use_cache=False)
        finally:
            first.close()
            second.close()

    assert len(server.requests) == 2
    assert server.requests[1][1] - server.requests[0][1] >= 1.0


def test_reservation_skips_during_cooldown_and_resumes_after_expiry(
    engine,
    session: Session,
) -> None:
    url = "https://race.netkeiba.com/example"
    now = datetime.datetime.now(datetime.UTC)
    session.add(
        FetchThrottleState(
            domain=_domain(url),
            next_allowed_at=now,
            blocked_until=now + datetime.timedelta(minutes=5),
            block_reason="http_429",
            updated_at=now,
        )
    )
    session.commit()
    gate = RequestPoliteness.for_engine(engine)

    blocked = gate.reserve(url)
    assert not blocked.allowed
    assert blocked.reason == "source_cooldown"

    row = session.get(FetchThrottleState, _domain(url))
    assert row is not None
    row.blocked_until = now - datetime.timedelta(seconds=1)
    session.commit()

    resumed = gate.reserve(url)
    assert resumed.allowed
    assert resumed.reason is None


def test_two_processes_can_initialize_the_same_domain_without_pk_failure(
    database_url: str,
) -> None:
    url = "https://race.netkeiba.com/concurrent-init"
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    processes = [
        ctx.Process(target=_reserve_in_process, args=(database_url, url, start, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    received = [results.get(timeout=15.0) for _ in processes]
    for process in processes:
        process.join(timeout=15.0)

    assert all(process.exitcode == 0 for process in processes)
    assert received == [(True, None), (True, None)]


def test_waiter_rereads_cooldown_and_sends_zero_requests(engine) -> None:
    url = "https://race.netkeiba.com/waiting"
    sleep_started = threading.Event()
    release_sleep = threading.Event()

    def controlled_sleep(_seconds: float) -> None:
        sleep_started.set()
        assert release_sleep.wait(timeout=5.0)

    seed = RequestPoliteness.for_engine(engine, min_interval_s=1.0)
    assert seed.reserve(url).allowed
    gate = RequestPoliteness.for_engine(
        engine,
        min_interval_s=1.0,
        sleep=controlled_sleep,
    )
    client = _SpyClient()
    fetcher = make_capture_fetcher(
        policy=gate,
        client=client,
        respect_robots=False,
    )
    caught: list[BaseException] = []

    def fetch() -> None:
        try:
            fetcher.get(url, use_cache=False)
        except BaseException as exc:  # noqa: BLE001 - asserted below
            caught.append(exc)

    thread = threading.Thread(target=fetch)
    thread.start()
    assert sleep_started.wait(timeout=5.0)
    gate.record_refusal(FetchRefused(429, url))
    release_sleep.set()
    thread.join(timeout=5.0)
    fetcher.close()

    assert not thread.is_alive()
    assert len(caught) == 1
    assert isinstance(caught[0], PolitenessRefused)
    assert caught[0].reason == "source_cooldown"
    assert client.calls == []


def test_concurrent_reservations_over_the_cap_skip_backlog(engine) -> None:
    url = "https://race.netkeiba.com/backlog"
    gate = RequestPoliteness.for_engine(
        engine,
        min_interval_s=1.0,
        max_wait_s=3.0,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _index: gate.reserve(url), range(8)))

    assert any(
        not decision.allowed and decision.reason == "throttle_backlog"
        for decision in decisions
    )
    assert all(
        decision.allowed or decision.reason == "throttle_backlog"
        for decision in decisions
    )


def test_real_fetcher_spaces_robots_and_main_request(engine) -> None:
    with _http_server() as (base_url, server):
        fetcher = make_capture_fetcher(engine=engine)
        try:
            assert fetcher.get(f"{base_url}/odds", use_cache=False) == '{"ok": true}'
        finally:
            fetcher.close()

    assert [path for path, _at in server.requests] == ["/robots.txt", "/odds"]
    assert server.requests[1][1] - server.requests[0][1] >= 1.0


def test_real_cli_odds_url_uses_the_canonical_scrape_domain_key(
    engine,
    session: Session,
) -> None:
    url = win_odds_url("202607260101")
    gate = RequestPoliteness.for_engine(engine)

    assert gate.reserve(url).allowed

    rows = list(session.scalars(select(FetchThrottleState)))
    assert [row.domain for row in rows] == [_domain(url)]


@pytest.mark.parametrize(
    ("status_code", "retry_after", "minimum_s", "maximum_s", "reason"),
    [
        (429, None, 29 * 60, 31 * 60, "http_429"),
        (403, None, 59 * 60, 61 * 60, "http_403"),
        (429, "90", 89, 91, "http_429"),
        (403, "999999", (6 * 60 * 60) - 1, (6 * 60 * 60) + 1, "http_403"),
    ],
)
def test_real_refusal_commits_cooldown_with_retry_after_cap(
    engine,
    status_code: int,
    retry_after: str | None,
    minimum_s: float,
    maximum_s: float,
    reason: str,
) -> None:
    url = f"https://race.netkeiba.com/refused-{status_code}-{retry_after}"
    client = _RefusedClient(
        _RefusedResponse(status_code, retry_after=retry_after)
    )
    fetcher = make_capture_fetcher(
        engine=engine,
        client=client,
        respect_robots=False,
    )
    before = datetime.datetime.now(datetime.UTC)
    try:
        with pytest.raises(FetchRefused):
            fetcher.get(url, use_cache=False)
    finally:
        fetcher.close()

    with Session(engine) as verify:
        row = verify.get(FetchThrottleState, _domain(url))
        assert row is not None
        assert row.block_reason == reason
        duration_s = (row.blocked_until - before).total_seconds()
    assert minimum_s <= duration_s <= maximum_s
    assert client.calls == [url]


def test_cooldown_commit_survives_an_unrelated_capture_session_rollback(
    engine,
    session: Session,
) -> None:
    url = "https://race.netkeiba.com/rollback-proof"
    gate = RequestPoliteness.for_engine(engine)

    session.scalar(select(func.now()))
    gate.record_refusal(FetchRefused(429, url))
    session.rollback()

    with Session(engine) as verify:
        row = verify.get(FetchThrottleState, _domain(url))
        assert row is not None
        assert row.block_reason == "http_429"
        assert row.blocked_until > datetime.datetime.now(datetime.UTC)


def test_capture_refusal_cooldown_survives_capture_transaction_rollback(
    engine,
    session: Session,
) -> None:
    seed_race(session)
    client = _RefusedClient(_RefusedResponse(429))
    raw_fetcher = make_capture_fetcher(
        engine=engine,
        client=client,
        min_interval_s=0.0,
    )
    deadline = time.monotonic() + 5.0
    raw_fetcher.set_deadline(deadline)
    try:
        report = capture_chaos(
            session,
            race_id=RACE_ID,
            fetcher=NetkeibaOddsFetcher(raw_fetcher),
            artifact=artifact_with_horizon(),
            capture_trigger=CAPTURE_TRIGGER,
            capture_policy_version=CAPTURE_POLICY_VERSION,
            deadline=deadline,
            clock=lambda: CAPTURED_AT,
        )
    finally:
        raw_fetcher.close()

    assert (report.status, report.reason) == ("skipped", "source_cooldown")
    session.rollback()

    with Session(engine) as verify:
        row = verify.get(FetchThrottleState, _domain(win_odds_url(RACE_ID)))
        assert row is not None
        assert row.block_reason == "http_429"
        assert row.blocked_until > datetime.datetime.now(datetime.UTC)


def test_fetch_refused_is_json_skip_and_cli_exit_zero(
    engine,
    session: Session,
    monkeypatch,
    capsys,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    seed_race(
        session,
        race_date=now.date(),
        post_time=now + datetime.timedelta(hours=1),
    )
    client = _RefusedClient(_RefusedResponse(429))
    raw_fetcher = make_capture_fetcher(
        engine=engine,
        client=client,
        min_interval_s=0.0,
    )
    monkeypatch.setattr(
        "horseracing_live.chaos_politeness.make_capture_fetcher",
        lambda **_kwargs: raw_fetcher,
    )
    args = SimpleNamespace(
        race_id=RACE_ID,
        date=None,
        min_seconds_to_post=0,
        trigger="explicit_command",
        json=True,
        capture_deadline_seconds=30.0,
        allow_outside_horizon=False,
        database_url=None,
    )

    assert cli._cmd_capture_chaos(session, args) == 0
    payload = json.loads(capsys.readouterr().out)

    assert (payload["outcome"], payload["reason"]) == (
        "skipped",
        "source_cooldown",
    )


def test_fetcher_deadline_covers_slow_robots_and_main_request(engine) -> None:
    budget_s = deadline_for("predict_manual")
    assert budget_s == 10.0
    with _http_server(delays={"/robots.txt": 6.0, "/odds": 6.0}) as (
        base_url,
        server,
    ):
        fetcher = make_capture_fetcher(
            engine=engine,
            deadline_s=budget_s,
        )
        started = time.monotonic()
        with pytest.raises(PolitenessRefused, match="deadline_exceeded") as exc_info:
            fetcher.get(f"{base_url}/odds", use_cache=False)
        elapsed = time.monotonic() - started
        fetcher.close()

    assert exc_info.value.reason == "deadline_exceeded"
    assert len(server.requests) == 2
    assert elapsed <= budget_s + 0.5


def test_set_deadline_cancels_inflight_request_on_cli_factory_path(engine) -> None:
    with _http_server(delays={"/robots.txt": 1.0}) as (base_url, server):
        fetcher = make_capture_fetcher(engine=engine)
        started = time.monotonic()
        fetcher.set_deadline(started + 0.2)
        with pytest.raises(PolitenessRefused, match="deadline_exceeded"):
            fetcher.get(f"{base_url}/odds", use_cache=False)
        elapsed = time.monotonic() - started
        fetcher.close()

    assert [path for path, _at in server.requests] == ["/robots.txt"]
    assert elapsed <= 0.7
