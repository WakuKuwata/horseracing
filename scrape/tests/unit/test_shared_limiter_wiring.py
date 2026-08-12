"""093: the wiring, not the policy.

The policy was already correct and covered; what was missing is that the ingest CLIs never used
it. These tests hold the seams in place — the ones that fail silently rather than loudly:

  * a refusal that no other process learns about,
  * a robots round-trip that skips the limiter,
  * and a bounded contract on a caller that has no deadline, which drops ingest instead of waiting.
"""

from __future__ import annotations

import pytest

from horseracing_scrape import cli
from horseracing_scrape.fetch import FetchRefused, HttpFetcher


class _Resp:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _Client:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.urls: list[str] = []

    def get(self, url):
        self.urls.append(url)
        return self._responses.pop(0)


def _fetcher(client, **kw) -> HttpFetcher:
    kw.setdefault("respect_robots", False)
    return HttpFetcher(
        user_agent="t", min_interval_s=0, client=client, sleep=lambda _s: None, **kw
    )


# --- the refusal seam -------------------------------------------------------

def test_a_refusal_is_reported_before_it_propagates():
    """Without this, only the process that got blocked backs off. The other streams keep sending
    into a source that is already refusing — which is how a temporary block becomes a long one."""
    seen: list[FetchRefused] = []
    f = _fetcher(_Client(_Resp(400)), on_refusal=seen.append)

    with pytest.raises(FetchRefused):
        f.get("https://race.netkeiba.com/x", use_cache=False)

    assert [e.status_code for e in seen] == [400]


def test_the_robots_request_reports_refusals_too():
    """robots.txt used to bypass BOTH the limiter and the refusal path, so a block discovered
    while fetching robots taught the system nothing."""
    seen: list[FetchRefused] = []
    client = _Client(_Resp(429))
    f = HttpFetcher(
        user_agent="t", min_interval_s=0, client=client, sleep=lambda _s: None,
        respect_robots=True, on_refusal=seen.append,
    )

    with pytest.raises(FetchRefused):
        f.get("https://race.netkeiba.com/x", use_cache=False)

    assert client.urls == ["https://race.netkeiba.com/robots.txt"]
    assert [e.status_code for e in seen] == [429]


def test_a_broken_reporter_does_not_swallow_the_refusal():
    """Bookkeeping must never turn "the source said no" into "something odd happened"."""
    def explode(_exc):
        raise RuntimeError("throttle table unreachable")

    f = _fetcher(_Client(_Resp(403)), on_refusal=explode)
    with pytest.raises(FetchRefused):
        f.get("https://race.netkeiba.com/x", use_cache=False)


def test_no_reporter_configured_is_the_old_behaviour():
    f = _fetcher(_Client(_Resp(400)))
    with pytest.raises(FetchRefused):
        f.get("https://race.netkeiba.com/x", use_cache=False)


# --- the CLI factory --------------------------------------------------------

def test_the_ingest_fetcher_uses_the_background_contract(monkeypatch):
    """`max_wait_s=3` against a 60s interval refuses every request after the first. A daily pass
    wired that way would look polite and quietly ingest nothing."""
    captured = {}

    def fake_policy(*, min_interval_s, database_url=None, **kw):
        captured["min_interval_s"] = min_interval_s
        captured["database_url"] = database_url
        return None

    monkeypatch.setattr(cli, "background_policy", fake_policy)
    cli._make_fetcher(60.0, None, "postgresql+psycopg://x/y")

    assert captured == {"min_interval_s": 60.0, "database_url": "postgresql+psycopg://x/y"}


def test_the_policy_is_attached_to_both_seams(monkeypatch):
    class _Policy:
        def pre_request(self, url): ...
        def record_refusal(self, exc, *, request_url=None): ...

    policy = _Policy()
    monkeypatch.setattr(cli, "background_policy", lambda **kw: policy)
    f = cli._make_fetcher(60.0, None)

    assert f._pre_request == policy.pre_request
    assert f._on_refusal == policy.record_refusal


def test_an_explicit_opt_out_leaves_the_local_limiter(monkeypatch):
    """Opting out is allowed; it just has to be a decision someone made, not a fallback."""
    monkeypatch.setattr(cli, "background_policy", lambda **kw: None)
    f = cli._make_fetcher(60.0, None)

    assert f._pre_request is None
    assert f._on_refusal is None
    assert f.min_interval_s == 60.0  # the per-instance floor still applies


def test_the_shared_limiter_can_be_switched_off_explicitly(monkeypatch):
    from horseracing_scrape.politeness import SHARED_LIMITER_ENV, background_policy

    monkeypatch.setenv(SHARED_LIMITER_ENV, "0")
    assert background_policy(min_interval_s=60.0) is None


def test_an_unreachable_limiter_refuses_to_scrape(monkeypatch):
    """FAIL CLOSED. Degrading to the per-instance limiter would restore every failure 093 exists
    to fix, and do it silently — which is worse than not scraping."""
    from horseracing_scrape import politeness

    monkeypatch.delenv(politeness.SHARED_LIMITER_ENV, raising=False)
    with pytest.raises(politeness.NoSharedLimiter, match=politeness.SHARED_LIMITER_ENV):
        politeness.background_policy(min_interval_s=60.0, database_url="not-a-url")


def test_the_send_slot_is_claimed_after_the_local_sleep_not_before():
    """Ordering matters: the shared reservation must be the LAST thing before the send. If the
    local interval slept after the claim, another process could legitimately send 60s after our
    claim but only seconds after our delayed request."""
    order: list[str] = []
    f = HttpFetcher(
        user_agent="t", min_interval_s=5.0, client=_Client(_Resp(200, "ok"), _Resp(200, "ok")),
        sleep=lambda _s: order.append("local-sleep"), respect_robots=False,
        pre_request=lambda _u: order.append("reserve"),
    )
    f.get("https://race.netkeiba.com/a", use_cache=False)
    f.get("https://race.netkeiba.com/b", use_cache=False)

    assert order == ["reserve", "local-sleep", "reserve"]


def test_the_cooldown_lands_on_the_first_refusal_not_after_the_retries():
    """A cooldown recorded only once retries are exhausted would let the retries go out first —
    into a source that already said no."""
    seen: list[int] = []
    client = _Client(_Resp(400))
    f = HttpFetcher(
        user_agent="t", min_interval_s=0, client=client, sleep=lambda _s: None,
        respect_robots=False, max_retries=3, on_refusal=lambda e: seen.append(e.status_code),
    )
    with pytest.raises(FetchRefused):
        f.get("https://race.netkeiba.com/x", use_cache=False)

    assert seen == [400]
    assert len(client.urls) == 1, "a refusal must not be retried"
