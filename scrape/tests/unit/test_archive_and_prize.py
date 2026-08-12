"""092: write-only page archive + 本賞金 parsing (network-free).

Two independent concerns share a file because they ship together:
  * the archive must never behave like a cache (that is the bug it exists to avoid), and
  * prize parsing must fail to None rather than write a wrong number into a top-gain feature.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from horseracing_scrape.fetch import FetchError, FetchRefused, HttpFetcher, archive_allowed
from horseracing_scrape.parse.entries import _prize_money, parse_entries

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "real"


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.status_code = status
        self.headers: dict[str, str] = {}


class _Client:
    """Counts real requests so a test can prove a fetch actually happened."""

    def __init__(self, body: bytes = b"<html>page</html>", status: int = 200):
        self._body, self._status, self.calls = body, status, 0

    def get(self, url, **kw):
        self.calls += 1
        return _Resp(self._body, self._status)


class _FlakyClient:
    """Fails with 500 the first n times, then serves 200 — for the retry/archive ordering test."""

    def __init__(self, fail_times: int, body: bytes = b"<html>ok</html>"):
        self._left, self._body, self.calls = fail_times, body, 0

    def get(self, url, **kw):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            return _Resp(b"", 500)
        return _Resp(self._body)


def _archived(root: Path) -> list[bytes]:
    return [gzip.open(p, "rb").read() for p in sorted(root.rglob("*.html.gz"))]


def _fetcher(tmp_path: Path, **kw) -> HttpFetcher:
    return HttpFetcher(
        user_agent="test", min_interval_s=0.0, respect_robots=False,
        sleep=lambda _s: None, **kw,
    )


# --- archive is not a cache -------------------------------------------------

def test_archive_always_refetches_and_keeps_every_observation(tmp_path):
    """The failure this guards: a results page fetched while the race was still pending being
    served forever, so the race never picks up its results."""
    client = _Client()
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=client)

    f.get("https://race.netkeiba.com/race/result.html?race_id=202601010101")
    f.get("https://race.netkeiba.com/race/result.html?race_id=202601010101")

    assert client.calls == 2, "second get() must hit the network, not replay a stored page"
    bodies = _archived(tmp_path / "arc")
    assert len(bodies) == 2, "append-only: both observations are kept, neither overwrites"
    assert bodies[0] == b"<html>page</html>"


def test_archive_stores_raw_bytes_not_the_lossy_decode(tmp_path):
    """db.netkeiba is EUC-JP and _resolve_text falls back to errors="replace". Archiving the
    decoded string would bake U+FFFD into the copy that exists precisely to be re-parsed."""
    raw = "本賞金:580万円".encode("euc_jp")
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=_Client(raw))
    text = f.get("https://db.netkeiba.com/race/202601010101/")

    assert _archived(tmp_path / "arc")[0] == raw, "bytes must round-trip exactly"
    assert _archived(tmp_path / "arc")[0].decode("euc_jp") == "本賞金:580万円"
    assert "�" in text, "the decoded return value is lossy here — the archive must not be"


def test_only_the_final_accepted_response_is_archived(tmp_path):
    """A 500 that retries into a 200 must leave exactly one archived body: the 200."""
    client = _FlakyClient(fail_times=2)
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=client)
    f.get("https://race.netkeiba.com/race/result.html?race_id=1")

    assert client.calls == 3
    assert _archived(tmp_path / "arc") == [b"<html>ok</html>"]


def test_exhausted_retries_archive_nothing(tmp_path):
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=_FlakyClient(fail_times=99))
    with pytest.raises(FetchError):
        f.get("https://race.netkeiba.com/race/result.html?race_id=1")
    assert _archived(tmp_path / "arc") == []


def test_archive_records_the_url_so_the_hash_is_reversible(tmp_path):
    url = "https://race.netkeiba.com/race/shutuba.html?race_id=202601010101"
    _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=_Client()).get(url)
    markers = list((tmp_path / "arc").rglob("url.txt"))
    assert len(markers) == 1
    assert markers[0].read_text(encoding="utf-8").strip() == url


@pytest.mark.parametrize("qs", ["?race_id=1&type=1", "?type=7&race_id=1", "", "?action=update"])
def test_no_odds_api_variant_is_ever_archived(tmp_path, qs):
    """Constitution V stores odds as a single latest value with no history; a timestamped archive
    of the odds endpoint would be that history through the back door. Every win/exotic quote
    variant shares this one path and differs only by query, so the rule matches on PATH — a
    substring or query-shaped check would let a reordered query slip through."""
    url = f"https://race.netkeiba.com/api/api_get_jra_odds.html{qs}"
    assert archive_allowed(url) is False
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=_Client(b'{"odds":1}'))
    f.get(url, use_cache=False)
    assert _archived(tmp_path / "arc") == []


def test_race_pages_are_allowed_by_the_archive_policy():
    for url in (
        "https://race.netkeiba.com/race/result.html?race_id=1",
        "https://race.netkeiba.com/race/shutuba.html?race_id=1",
        "https://db.netkeiba.com/horse/2022103995/",
    ):
        assert archive_allowed(url) is True


def test_refusal_page_is_never_archived(tmp_path):
    """netkeiba answers a block with HTTP 400 and an empty body. Archiving that as if it were a
    race page would silently poison every future re-parse of that race."""
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=_Client(b"", status=400))
    with pytest.raises(FetchRefused):
        f.get("https://race.netkeiba.com/race/result.html?race_id=202601010101")
    assert _archived(tmp_path / "arc") == []


def test_cache_and_archive_cannot_be_enabled_together(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        _fetcher(tmp_path, cache_dir=tmp_path / "c", archive_dir=tmp_path / "a")


def test_archive_failure_never_breaks_the_fetch(tmp_path):
    """A full disk must degrade to "no archive", not take down the daily scrape."""
    blocker = tmp_path / "arc"
    blocker.write_text("not a directory")  # mkdir under this path will raise
    f = _fetcher(tmp_path, archive_dir=blocker, client=_Client())
    assert f.get("https://race.netkeiba.com/race/result.html?race_id=1") == "<html>page</html>"


def test_same_microsecond_writes_do_not_lose_an_observation(tmp_path, monkeypatch):
    """Two I/O threads can land in the same timestamp; neither may overwrite the other."""
    import horseracing_scrape.fetch as fetch_mod

    class _FrozenClock:
        @staticmethod
        def now(tz=None):
            import datetime as _dt
            return _dt.datetime(2026, 8, 12, 3, 4, 5, 678901, tzinfo=_dt.UTC)

    monkeypatch.setattr(fetch_mod, "datetime", _FrozenClock)
    f = _fetcher(tmp_path, archive_dir=tmp_path / "arc", client=_Client())
    url = "https://race.netkeiba.com/race/result.html?race_id=1"
    f.get(url)
    f.get(url)
    assert len(_archived(tmp_path / "arc")) == 2


def test_no_archive_dir_writes_nothing(tmp_path):
    f = _fetcher(tmp_path, client=_Client())
    f.get("https://race.netkeiba.com/race/result.html?race_id=1")
    assert list(tmp_path.rglob("*.gz")) == []


# --- 本賞金 -----------------------------------------------------------------

def test_prize_from_real_entries_fixture():
    html = (_FIXTURES / "entries_202406050911.html").read_text(encoding="utf-8")
    assert parse_entries(html).race.prize_money == 7000  # 本賞金:7000,2800,1800,1100,700万円


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5回 中山 9日目 本賞金:7000,2800,1800,1100,700万円", 7000),
        ("1回 函館 12日目 本賞金:580,230,150,87,58万円", 580),   # matches DB 万円 units
        ("本賞金：580,230,150,87,58万円", 580),                   # full-width colon
        ("本賞金: 580,230,150,87,58 万円", 580),                  # spacing
        ("本賞金:40000,16000,10000,6000,4000万円", 40000),        # G1-scale, no separator
        ("5回 中山 9日目 サラ系２歳 オープン 18頭", None),          # absent -> None, not a raise
        ("", None),
        ("本賞金:1,0000,500万円", None),   # ascending => comma is a thousands separator, not places
        ("本賞金:999999,1万円", None),     # implausible 1着 => we misread it
        ("本賞金:0,0万円", None),
        ("本賞金：５８０,２３０万円", 580),   # full-width digits + colon (NFKC)
    ],
)
def test_prize_parsing_and_its_refusals(text, expected):
    assert _prize_money(text) == expected


def test_prize_absence_does_not_break_entry_parsing():
    """Prize is optional metadata: a page that stops showing it must still ingest its entries."""
    html = (_FIXTURES / "entries_202406050911.html").read_text(encoding="utf-8")
    stripped = html.replace("本賞金", "旧賞金")
    parsed = parse_entries(stripped)
    assert parsed.race.prize_money is None
    assert len(parsed.horses) == len(parse_entries(html).horses)
