"""176 T042: index → day → page navigation on the saved pages; cname never leaves JraPageRef."""

from __future__ import annotations

import datetime
import pickle

import pytest

from horseracing_scrape.jra.errors import JraParameterError
from horseracing_scrape.jra.models import JraPageRef, JraResponse
from horseracing_scrape.jra.navigate import (
    INDEX,
    Walker,
    day_links,
    links,
    race_links,
)
from tests.jra_fixtures import jra_text

D = datetime.date(2026, 11, 1)
D_PREV = datetime.date(2026, 10, 31)


def test_fixed_index_entry_points():
    assert INDEX["index_d"].cname == "pw01dli00/F3"
    assert INDEX["index_o"].cname == "pw15oli00/6D"
    assert INDEX["index_s"].cname == "pw01sli00/AF"
    assert INDEX["index_h"].cname == "pw01hli00/03"


def test_page_ref_hides_cname_from_repr_and_cannot_be_pickled():
    ref = JraPageRef(
        "https://www.jra.go.jp/JRADB/accessD.html", "secret-cname/3F", label="4回東京11日"
    )
    assert "secret-cname" not in repr(ref)
    with pytest.raises(TypeError):
        pickle.dumps(ref)


def test_day_links_match_date_and_label():
    idx = jra_text("index_d_20261101.sjis.html")
    days = day_links(idx, D, access="accessD.html", kind_prefix="pw01drl")
    venues = sorted(d.venue_code for d in days)
    assert venues == ["05", "08"]  # 東京・京都
    tokyo = next(d for d in days if d.venue_code == "05")
    assert (tokyo.kai, tokyo.nichime, tokyo.race_date) == (4, 11, D)
    assert (
        day_links(idx, datetime.date(2026, 12, 25), access="accessD.html", kind_prefix="pw01drl")
        == []
    )


def test_day_list_has_the_entries_page_link():
    day = jra_text("daylist_d_tokyo_20261101.sjis.html")
    pages = day_links(day, D, access="accessD.html", kind_prefix="pw01des")
    assert [p.venue_code for p in pages] == ["05"]


def test_results_index_links_previous_day():
    idx = jra_text("index_s_20261101.sjis.html")
    days = day_links(idx, D_PREV, access="accessS.html", kind_prefix="pw01srl")
    assert "05" in {d.venue_code for d in days}
    day = jra_text("daylist_s_tokyo_20261031.sjis.html")
    pages = day_links(day, D_PREV, access="accessS.html", kind_prefix="pw01ses")
    assert len(pages) == 1 and pages[0].venue_code == "05"


def test_odds_day_page_has_twelve_race_links():
    refs = race_links(jra_text("daylist_o_tokyo_20261101.sjis.html"))
    assert len(refs) == 12
    assert all(r.kind == "odds_win" for r in refs)
    assert all(r.cname.startswith("pw151ou") for r in refs)


def test_links_helper_filters_by_access_page():
    html = jra_text("daylist_d_tokyo_20261101.sjis.html")
    assert all(u.endswith("accessO.html") for u, _, _ in links(html, path_suffix="accessO.html"))


class _FakeFetcher:
    """Serves fixture pages by (url suffix, cname prefix); raises JraParameterError when told."""

    def __init__(self, fail_first_for: str | None = None):
        self.calls: list[tuple[str, str]] = []
        self._fail = fail_first_for
        self.requests_sent = 0

    def post(self, url, cname, *, kind):
        self.calls.append((url, cname))
        self.requests_sent += 1
        if self._fail and cname.startswith(self._fail):
            self._fail = None
            raise JraParameterError("boom", url=url)
        if cname == "pw01dli00/F3":
            name = "index_d_20261101.sjis.html"
        elif cname.startswith("pw01drl0005"):
            name = "daylist_d_tokyo_20261101.sjis.html"
        elif cname.startswith("pw01des0105"):
            name = "entries_tokyo_20261101.sjis.html"
        elif cname == "pw01sli00/AF":
            name = "index_s_20261101.sjis.html"
        elif cname.startswith("pw01srl1005"):
            name = "daylist_s_tokyo_20261031.sjis.html"
        elif cname.startswith("pw01ses1005"):
            name = "results_tokyo_20261031.sjis.html"
        else:
            raise JraParameterError(f"no fixture for {cname[:10]}", url=url)
        text = jra_text(name)
        return JraResponse(url, "x", 200, b"", text, datetime.datetime.now(datetime.UTC), kind, "y")


def test_walker_entries_tokyo():
    f = _FakeFetcher()
    w = Walker(f, date=D)
    pages = w.day_pages("entries", venue="05")
    assert len(pages) == 1 and pages[0][0].venue_code == "05"
    assert "race_header" in pages[0][1].text
    assert [c[:7] for _, c in f.calls] == ["pw01dli", "pw01drl", "pw01des"]
    assert w.report.rewalks == 0 and w.report.unsupported_date == 0


def test_walker_results_previous_day():
    f = _FakeFetcher()
    pages = Walker(f, date=D_PREV).day_pages("results", venue="05")
    assert len(pages) == 1 and "refund_area" in pages[0][1].text


def test_walker_unsupported_date():
    f = _FakeFetcher()
    w = Walker(f, date=datetime.date(2026, 12, 25))
    assert w.day_pages("entries") == []
    assert w.report.unsupported_date == 1


def test_walker_rewalks_once_then_fails():
    f = _FakeFetcher(fail_first_for="pw01des")
    w = Walker(f, date=D)
    pages = w.day_pages("entries", venue="05")
    assert len(pages) == 1 and w.report.rewalks == 1
    # the walk restarts FROM THE INDEX (fresh cnames); the failed POST is not just re-sent
    assert [c[:7] for _, c in f.calls] == [
        "pw01dli",
        "pw01drl",
        "pw01des",
        "pw01dli",
        "pw01drl",
        "pw01des",
    ]
    # a second failure in the same walker is final
    f2 = _FakeFetcher(fail_first_for="pw01des")
    w2 = Walker(f2, date=D)
    w2._rewalks = 1
    with pytest.raises(JraParameterError):
        w2.day_pages("entries", venue="05")
