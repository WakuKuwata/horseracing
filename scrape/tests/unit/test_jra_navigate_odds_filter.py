"""176 T046a: `--race-id` limits the per-race 単複 POSTs by the race number carried in the cname."""

from __future__ import annotations

import datetime

from horseracing_scrape.jra.models import JraResponse
from horseracing_scrape.jra.navigate import Walker, odds_cname_race_no
from tests.jra_fixtures import jra_text

D = datetime.date(2026, 11, 1)
_INDEX_O = (
    '<html><body><a href="#" onclick="return doAction(\'/JRADB/accessO.html\', '
    "'pw15ols00052026041120261101/00');\">4回東京11日</a></body></html>"
)


class _Fetcher:
    def __init__(self):
        self.calls: list[str] = []
        self.requests_sent = 0

    def post(self, url, cname, *, kind):
        self.calls.append(cname)
        self.requests_sent += 1
        if cname == "pw15oli00/6D":
            text = _INDEX_O
        elif cname.startswith("pw15ols0005"):
            text = jra_text("daylist_o_tokyo_20261101.sjis.html")
        else:
            text = "<html><body>race page</body></html>"
        return JraResponse(url, "x", 200, b"", text, datetime.datetime.now(datetime.UTC), kind, "y")


def test_race_no_is_read_from_the_cname_not_the_label():
    assert odds_cname_race_no("pw151ouS305202604111120261101Z/--") == 11
    assert odds_cname_race_no("pw151ouS305202604110120261101Z/--") == 1
    assert (
        odds_cname_race_no("pw153ouS305202604111120261101Z/--") == 11
    )  # other bet-type page: same shape
    assert odds_cname_race_no("pw01des01052026041120261101/--") is None


def test_want_limits_the_race_posts_to_the_requested_numbers():
    f = _Fetcher()
    pages = list(Walker(f, date=D).odds_pages(venue="05", want={("05", 11), ("05", 3)}))
    posted = [c for c in f.calls if c.startswith("pw151ou")]
    assert len(pages) == 2 and len(posted) == 2
    assert sorted(odds_cname_race_no(c) for c in posted) == [3, 11]
    # a venue that is not on the page → nothing posted beyond the index and the day page
    f2 = _Fetcher()
    assert list(Walker(f2, date=D).odds_pages(venue="05", want={("08", 11)})) == []
    assert f2.requests_sent == 2


def test_without_want_every_race_page_is_fetched_once():
    f = _Fetcher()
    pages = list(Walker(f, date=D).odds_pages(venue="05"))
    posted = [c for c in f.calls if c.startswith("pw151ou")]
    assert len(pages) == 12 and len(posted) == 12 and len(set(posted)) == 12
