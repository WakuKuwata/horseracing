"""176 T038/T039/T040: JraFetcher response classification, strict decoding, archive allow-list.

Network-free: httpx.MockTransport. The shared politeness policy is a fake that records calls.
"""

from __future__ import annotations

import datetime
import hashlib

import httpx
import pytest

from horseracing_scrape import robots_cache
from horseracing_scrape.fetch import FetchError, FetchRefused, RobotsDisallowed
from horseracing_scrape.jra.errors import JraDecodeError, JraParameterError
from horseracing_scrape.jra.fetch import CaptureWindowRefused, JraFetcher, _decode
from horseracing_scrape.jra.models import KIND_ENTRIES, KIND_INDEX_D, KIND_ODDS_WIN, KIND_RESULTS
from tests.jra_fixtures import jra_raw

URL = "https://www.jra.go.jp/JRADB/accessD.html"
ROBOTS = "https://www.jra.go.jp/robots.txt"


class FakePolicy:
    def __init__(self):
        self.pre = []
        self.refusals = []

    def pre_request(self, url):
        self.pre.append(url)

    def record_refusal(self, refusal, *, request_url=None):
        self.refusals.append((refusal.status_code, refusal.url, refusal.retry_after_s))


def _fetcher(handler, *, policy=None, guard=None, archive_dir=None, robots=True, cache=None):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, follow_redirects=False)
    return JraFetcher(
        policy=policy or FakePolicy(),
        capture_guard=guard,
        archive_dir=archive_dir,
        client=client,
        respect_robots=robots,
        robots_cache_store=cache,
        sleep=lambda s: None,
    )


def _robots_ok(request):
    return httpx.Response(200, content=b"User-agent: *\nDisallow:\n")


def _page(body: bytes, status=200, headers=None):
    return httpx.Response(status, content=body, headers=headers or {"Content-Type": "text/html"})


SJIS_OK = '<html><head><meta charset="Shift_JIS"><title>出馬表</title></head><body>x</body></html>'.encode(
    "cp932"
)


# --- decoding ---------------------------------------------------------------------


def test_decode_header_charset_wins_then_meta_then_cp932_default():
    assert _decode("あ".encode("cp932"), "text/html; charset=Shift_JIS") == "あ"
    assert _decode(b'<meta charset="utf-8">\xe3\x81\x82', None) == '<meta charset="utf-8">あ'
    assert _decode("あ".encode("cp932"), "text/html") == "あ"  # no charset anywhere → cp932


@pytest.mark.parametrize("alias", ["shift_jis", "sjis", "x-sjis", "windows-31j", "ms932"])
def test_sjis_aliases_decode_as_cp932_with_extension_chars(alias):
    # ① and 髙 are cp932 extension characters that strict shift_jis would reject
    raw = "①髙".encode("cp932")
    assert _decode(raw, f"text/html; charset={alias}") == "①髙"


def test_decode_is_strict():
    with pytest.raises(JraDecodeError):
        _decode(b"\xff\xfe\x81", "text/html; charset=Shift_JIS")


def test_fixtures_decode_through_the_fetcher_path():
    for name in (
        "entries_tokyo_20261101.sjis.html",
        "results_tokyo_20261031.sjis.html",
        "odds_tokyo11r_20261101_0822.sjis.html",
        "accessJ_20261101.json",
    ):
        _decode(jra_raw(name), "text/html")


# --- classification ---------------------------------------------------------------


def test_200_ok_counts_requests_and_hashes():
    pol = FakePolicy()
    f = _fetcher(
        lambda r: _robots_ok(r) if r.url.path == "/robots.txt" else _page(SJIS_OK), policy=pol
    )
    resp = f.post(URL, "pw01dli00/F3", kind=KIND_INDEX_D)
    assert resp.status == 200 and "出馬表" in resp.text
    assert resp.raw_sha256 == hashlib.sha256(SJIS_OK).hexdigest()
    assert resp.cname_sha256 == hashlib.sha256(b"pw01dli00/F3").hexdigest()
    assert f.requests_sent == 2  # robots + POST
    assert pol.pre == [ROBOTS, URL]  # every send reserved a slot, robots included


def test_200_parameter_error_screen():
    raw = jra_raw("error_param.sjis.html")
    f = _fetcher(lambda r: _page(raw), robots=False)
    with pytest.raises(JraParameterError):
        f.post(URL, "pw01dli00/00", kind=KIND_INDEX_D)
    assert f.requests_sent == 1  # never retried


def test_redirect_to_error013_and_other_redirects_are_parameter_errors_without_follow():
    calls = []

    def handler(r):
        calls.append(str(r.url))
        return httpx.Response(301, headers={"Location": "http://www.jra.go.jp/error/error013.html"})

    f = _fetcher(handler, robots=False)
    with pytest.raises(JraParameterError):
        f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert calls == [URL]  # 3xx is not followed: zero extra sends
    f2 = _fetcher(
        lambda r: httpx.Response(302, headers={"Location": "https://elsewhere/"}), robots=False
    )
    with pytest.raises(JraParameterError):
        f2.post(URL, "x/00", kind=KIND_INDEX_D)


def test_400_is_a_parameter_error_without_cooldown():
    pol = FakePolicy()
    f = _fetcher(lambda r: httpx.Response(400), policy=pol, robots=False)
    with pytest.raises(JraParameterError):
        f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert pol.refusals == []


@pytest.mark.parametrize("status", [403, 429])
def test_403_429_refuse_and_install_cooldown(status):
    pol = FakePolicy()
    f = _fetcher(
        lambda r: httpx.Response(status, headers={"Retry-After": "120"}), policy=pol, robots=False
    )
    with pytest.raises(FetchRefused):
        f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert pol.refusals == [(status, URL, 120.0)]
    assert f.requests_sent == 1


def test_503_with_retry_after_refuses_without_it_is_a_plain_error():
    pol = FakePolicy()
    f = _fetcher(
        lambda r: httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}),
        policy=pol,
        robots=False,
    )
    with pytest.raises(FetchRefused):
        f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert pol.refusals and pol.refusals[0][0] == 503
    f2 = _fetcher(lambda r: httpx.Response(503), robots=False)
    with pytest.raises(FetchError):
        f2.post(URL, "x/00", kind=KIND_INDEX_D)
    assert f2.requests_sent == 1  # POST is never retried


def test_get_retries_once_on_5xx_post_never():
    n = {"c": 0}

    def flaky(r):
        n["c"] += 1
        return httpx.Response(500) if n["c"] == 1 else _page(SJIS_OK)

    f = _fetcher(flaky, robots=False)
    assert (
        f.get("https://www.jra.go.jp/keiba/baba/_data_cushion.html", kind="baba_fragment").status
        == 200
    )
    assert f.requests_sent == 2
    n["c"] = 0
    f2 = _fetcher(lambda r: httpx.Response(500), robots=False)
    with pytest.raises(FetchError):
        f2.get(URL, kind=KIND_INDEX_D)
    assert f2.requests_sent == 2  # GET: exactly one retry


def test_decode_failure_discards_the_response():
    f = _fetcher(
        lambda r: _page(
            b"\xff\xfe\x81\x00", headers={"Content-Type": "text/html; charset=Shift_JIS"}
        ),
        robots=False,
    )
    with pytest.raises(JraDecodeError):
        f.post(URL, "x/00", kind=KIND_INDEX_D)


# --- robots -----------------------------------------------------------------------------


def test_robots_cache_hit_sends_nothing_and_403_robots_refuses(tmp_path):
    cache = robots_cache.RobotsCache(tmp_path)
    cache.put(
        "https://www.jra.go.jp",
        outcome=robots_cache.RULES,
        status=200,
        body=b"User-agent: *\nDisallow:\n",
    )
    f = _fetcher(lambda r: _page(SJIS_OK), cache=cache)
    f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert f.requests_sent == 1  # robots served from cache
    # expired → re-fetched once
    stale = robots_cache.RobotsCache(tmp_path, ttl_s=0)
    f2 = _fetcher(
        lambda r: _robots_ok(r) if r.url.path == "/robots.txt" else _page(SJIS_OK), cache=stale
    )
    f2.post(URL, "x/00", kind=KIND_INDEX_D)
    assert f2.requests_sent == 2
    pol = FakePolicy()
    f3 = _fetcher(lambda r: httpx.Response(403), policy=pol)
    with pytest.raises(FetchRefused):
        f3.post(URL, "x/00", kind=KIND_INDEX_D)
    assert pol.refusals[0][0] == 403 and f3.requests_sent == 1


def test_robots_disallow_blocks_without_sending_the_page():
    f = _fetcher(
        lambda r: (
            httpx.Response(200, content=b"User-agent: *\nDisallow: /JRADB/\n")
            if r.url.path == "/robots.txt"
            else _page(SJIS_OK)
        )
    )
    with pytest.raises(RobotsDisallowed):
        f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert f.requests_sent == 1


# --- capture window guard ---------------------------------------------------------------


def test_capture_guard_checked_before_and_after_the_slot():
    state = {"open": False}
    pol = FakePolicy()

    def pre(url):  # the window opens WHILE waiting for the slot
        pol.pre.append(url)
        state["open"] = True

    pol.pre_request = pre
    f = _fetcher(
        lambda r: _page(SJIS_OK),
        policy=pol,
        robots=False,
        guard=lambda: ("window", "202605041111") if state["open"] else None,
    )
    with pytest.raises(CaptureWindowRefused):
        f.post(URL, "x/00", kind=KIND_INDEX_D)
    assert f.requests_sent == 0


# --- archive allow-list -----------------------------------------------------------------


def test_archive_keeps_entries_but_never_odds_and_hides_cname(tmp_path):
    f = _fetcher(lambda r: _page(SJIS_OK), robots=False, archive_dir=tmp_path)
    # synthetic cnames (hyphens): real navigation cnames must not appear in test code (T003)
    f.post(URL, "entries-cname/50", kind=KIND_ENTRIES)
    f.post("https://www.jra.go.jp/JRADB/accessO.html", "odds-cname/49", kind=KIND_ODDS_WIN)
    f.post("https://www.jra.go.jp/JRADB/accessS.html", "results-cname/87", kind=KIND_RESULTS)
    files = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    html = [p for p in files if p.suffix == ".html"]
    assert len(html) == 2  # entries + results; the odds page was not retained
    for marker in (p for p in files if p.name == "url.txt"):
        txt = marker.read_text(encoding="utf-8")
        assert "accessO" not in txt
        assert "entries-cname" not in txt and "results-cname" not in txt
        assert "cname_sha256=" in txt


def test_fetched_at_uses_the_injected_clock():
    fixed = datetime.datetime(2026, 11, 1, 8, 22, tzinfo=datetime.UTC)
    f = JraFetcher(
        policy=FakePolicy(),
        client=httpx.Client(transport=httpx.MockTransport(lambda r: _page(SJIS_OK))),
        respect_robots=False,
        clock=lambda: fixed,
    )
    assert f.post(URL, "x/00", kind=KIND_INDEX_D).fetched_at == fixed
