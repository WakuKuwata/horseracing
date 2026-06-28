"""Foundational (FR-001/C1): HttpFetcher politeness — robots, rate-limit, backoff, cache."""

from __future__ import annotations

import pytest

from horseracing_scrape.fetch import FetchError, HttpFetcher, RobotsDisallowed


class _Resp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _Client:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        r = self.responses[url]
        return r.pop(0) if isinstance(r, list) else r


def test_robots_disallow_blocks_fetch():
    client = _Client({
        "https://x.com/robots.txt": _Resp(200, "User-agent: *\nDisallow: /secret\n"),
        "https://x.com/secret": _Resp(200, "<html>"),
        "https://x.com/ok": _Resp(200, "OK"),
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: None, clock=lambda: 100.0)
    with pytest.raises(RobotsDisallowed):
        f.get("https://x.com/secret")
    assert f.get("https://x.com/ok") == "OK"  # non-disallowed path allowed


def test_rate_limit_waits_between_fetches():
    sleeps: list[float] = []
    client = _Client({
        "https://x.com/robots.txt": _Resp(200, ""),
        "https://x.com/a": _Resp(200, "a"), "https://x.com/b": _Resp(200, "b"),
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: sleeps.append(s),
                    clock=lambda: 100.0, min_interval_s=2.0)
    f.get("https://x.com/a")
    f.get("https://x.com/b")
    assert any(s >= 2.0 for s in sleeps)  # second fetch waited the min interval


def test_exponential_backoff_then_success():
    sleeps: list[float] = []
    client = _Client({
        "https://x.com/robots.txt": _Resp(200, ""),
        "https://x.com/p": [_Resp(500, ""), _Resp(200, "OK")],
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: sleeps.append(s),
                    clock=lambda: 100.0, max_retries=3)
    assert f.get("https://x.com/p") == "OK"
    assert 1.0 in sleeps  # backoff slept before retry


def test_all_failures_raise():
    client = _Client({
        "https://x.com/robots.txt": _Resp(200, ""),
        "https://x.com/p": [_Resp(500, ""), _Resp(500, ""), _Resp(500, "")],
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: None,
                    clock=lambda: 100.0, max_retries=3)
    with pytest.raises(FetchError):
        f.get("https://x.com/p")


class _BytesResp:
    """Mimics httpx.Response: exposes content + charset_encoding (from Content-Type header)."""
    def __init__(self, status_code: int, content: bytes, charset_encoding: str | None):
        self.status_code = status_code
        self.content = content
        self.charset_encoding = charset_encoding

    @property
    def text(self) -> str:  # httpx would (mis)guess when no header charset
        return self.content.decode("utf-8", errors="replace")


def test_eucjp_page_without_header_charset_decodes_from_meta():
    # db.netkeiba.com: EUC-JP body, NO charset in Content-Type → must sniff <meta charset>
    body = (
        "<html><head><meta charset=euc-jp></head>"
        "<body><h1>ジョバンニ</h1></body></html>"
    ).encode("euc_jp")
    client = _Client({
        "https://db.x.com/robots.txt": _Resp(200, ""),
        "https://db.x.com/h": _BytesResp(200, body, charset_encoding=None),
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: None, clock=lambda: 100.0)
    assert "ジョバンニ" in f.get("https://db.x.com/h")  # decoded, not mojibake


def test_header_charset_trusts_httpx_text():
    # when the header carries the charset, httpx already decoded — use .text as-is
    client = _Client({
        "https://x.com/robots.txt": _Resp(200, ""),
        "https://x.com/u": _BytesResp(200, "café".encode(), charset_encoding="utf-8"),
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: None, clock=lambda: 100.0)
    assert f.get("https://x.com/u") == "café"


def test_cache_avoids_second_fetch(tmp_path):
    client = _Client({
        "https://x.com/robots.txt": _Resp(200, ""), "https://x.com/p": _Resp(200, "PAGE"),
    })
    f = HttpFetcher(user_agent="UA", client=client, sleep=lambda s: None, clock=lambda: 100.0,
                    cache_dir=tmp_path)
    assert f.get("https://x.com/p") == "PAGE"
    before = client.calls.count("https://x.com/p")
    assert f.get("https://x.com/p") == "PAGE"  # served from cache
    assert client.calls.count("https://x.com/p") == before
