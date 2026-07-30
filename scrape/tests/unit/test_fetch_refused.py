"""Feature 086: refusal responses stop immediately and request hooks cover every request."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from horseracing_scrape.fetch import FetchRefused, HttpFetcher

_MAIN_URL = "https://x.com/data"
_ROBOTS_URL = "https://x.com/robots.txt"


class _Resp:
    def __init__(
        self,
        status_code: int,
        text: str = "",
        *,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _Client:
    def __init__(self, responses: dict[str, _Resp | list[_Resp]]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str) -> _Resp:
        self.calls.append(url)
        response = self.responses[url]
        return response.pop(0) if isinstance(response, list) else response


def _fetcher(
    client: _Client,
    *,
    respect_robots: bool,
    pre_request: Callable[[str], None] | None = None,
    max_retries: int = 3,
    sleeps: list[float] | None = None,
) -> HttpFetcher:
    return HttpFetcher(
        user_agent="test-agent",
        client=client,
        respect_robots=respect_robots,
        pre_request=pre_request,
        max_retries=max_retries,
        sleep=(lambda seconds: sleeps.append(seconds)) if sleeps is not None else lambda _: None,
        clock=lambda: 100.0,
    )


@pytest.mark.parametrize(
    ("status_code", "headers", "expected_retry_after_s"),
    [
        (403, {}, None),
        (429, {"Retry-After": "99999"}, 6 * 60 * 60),
    ],
)
def test_main_refusal_raises_on_first_attempt(
    status_code: int,
    headers: dict[str, str],
    expected_retry_after_s: float | None,
):
    client = _Client({_MAIN_URL: _Resp(status_code, headers=headers)})
    fetcher = _fetcher(client, respect_robots=False)

    with pytest.raises(FetchRefused) as raised:
        fetcher.get(_MAIN_URL, use_cache=False)

    assert raised.value.status_code == status_code
    assert raised.value.retry_after_s == expected_retry_after_s
    assert client.calls == [_MAIN_URL]


def test_robots_429_refuses_without_main_request():
    client = _Client({
        _ROBOTS_URL: _Resp(429),
        _MAIN_URL: _Resp(200, "must not be fetched"),
    })
    fetcher = _fetcher(client, respect_robots=True)

    with pytest.raises(FetchRefused) as raised:
        fetcher.get(_MAIN_URL, use_cache=False)

    assert raised.value.status_code == 429
    assert client.calls == [_ROBOTS_URL]
    assert client.calls.count(_MAIN_URL) == 0


def test_robots_403_still_allows_main_request():
    client = _Client({
        _ROBOTS_URL: _Resp(403),
        _MAIN_URL: _Resp(200, "payload"),
    })
    fetcher = _fetcher(client, respect_robots=True)

    assert fetcher.get(_MAIN_URL, use_cache=False) == "payload"
    assert client.calls == [_ROBOTS_URL, _MAIN_URL]


@pytest.mark.parametrize(
    ("respect_robots", "expected_hook_url"),
    [
        (True, _ROBOTS_URL),
        (False, _MAIN_URL),
    ],
)
def test_pre_request_refusal_sends_no_http_requests(
    respect_robots: bool,
    expected_hook_url: str,
):
    client = _Client({})
    hook_calls: list[str] = []

    def refuse(url: str) -> None:
        hook_calls.append(url)
        raise FetchRefused(429, url)

    fetcher = _fetcher(
        client,
        respect_robots=respect_robots,
        pre_request=refuse,
    )

    with pytest.raises(FetchRefused):
        fetcher.get(_MAIN_URL, use_cache=False)

    assert hook_calls == [expected_hook_url]
    assert client.calls == []


def test_other_non_200_responses_still_retry_within_budget():
    sleeps: list[float] = []
    client = _Client({
        _MAIN_URL: [_Resp(500), _Resp(502), _Resp(200, "payload")],
    })
    fetcher = _fetcher(
        client,
        respect_robots=False,
        max_retries=3,
        sleeps=sleeps,
    )

    assert fetcher.get(_MAIN_URL, use_cache=False) == "payload"
    assert client.calls == [_MAIN_URL, _MAIN_URL, _MAIN_URL]
    assert sleeps == [1.0, 2.0]


def test_bare_400_is_a_refusal_not_an_ordinary_error():
    """netkeiba answers a sustained-load block with HTTP 400 and an EMPTY body — on the odds API
    and on plain HTML pages alike, including URLs that served fine moments earlier. Classifying
    that as an ordinary error means the retry/backoff and the caller's stop-the-pass logic never
    engage, so a block turns into hundreds more refused requests."""
    from horseracing_scrape.fetch import REFUSAL_STATUSES
    assert 400 in REFUSAL_STATUSES
    assert {403, 429}.issubset(REFUSAL_STATUSES)


def test_bare_400_is_raised_and_not_retried():
    client = _Client({_MAIN_URL: _Resp(400, ""), _ROBOTS_URL: _Resp(200, "")})
    with pytest.raises(FetchRefused) as e:
        _fetcher(client, respect_robots=False).get(_MAIN_URL, use_cache=False)
    assert e.value.status_code == 400
    main_calls = [u for u in client.calls if u == _MAIN_URL]
    assert len(main_calls) == 1, f"a refusal must not be retried, got {len(main_calls)} attempts"
