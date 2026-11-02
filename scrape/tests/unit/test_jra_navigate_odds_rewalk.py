"""176: a failed 単複 race page re-walks index → day page once and posts the fresh link."""

from __future__ import annotations

import datetime

import pytest

from horseracing_scrape.jra.errors import JraParameterError
from horseracing_scrape.jra.navigate import Walker, odds_cname_race_no
from tests.unit.test_jra_navigate_odds_filter import _Fetcher

D = datetime.date(2026, 11, 1)


class _FailOnRace(_Fetcher):
    def __init__(self, race_no: int, times: int):
        super().__init__()
        self._no, self._left = race_no, times

    def post(self, url, cname, *, kind):
        if cname.startswith("pw151ou") and odds_cname_race_no(cname) == self._no and self._left:
            self._left -= 1
            self.calls.append(cname)
            self.requests_sent += 1
            raise JraParameterError("stale", url=url)
        return super().post(url, cname, kind=kind)


def test_failed_race_page_rewalks_from_the_index_once():
    f = _FailOnRace(11, times=1)
    w = Walker(f, date=D)
    pages = list(w.odds_pages(venue="05", want={("05", 11)}))
    assert len(pages) == 1 and w.report.rewalks == 1
    assert [c[:7] for c in f.calls] == [
        "pw15oli",
        "pw15ols",
        "pw151ou",
        "pw15oli",
        "pw15ols",
        "pw151ou",
    ]


def test_second_failure_is_final():
    f = _FailOnRace(11, times=2)
    with pytest.raises(JraParameterError):
        list(Walker(f, date=D).odds_pages(venue="05", want={("05", 11)}))
