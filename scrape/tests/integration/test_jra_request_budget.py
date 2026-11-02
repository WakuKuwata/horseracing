"""176 T096a (SC-005): one race day of 2 venues × 12 races stays within the request budget.

entries → odds (all 24 races) → results → baba through run_command with a fixture fetcher. Kyoto
pages are the Tokyo fixtures with the venue rewritten (only the request count is measured here;
what they write is covered elsewhere). robots.txt is one request on the real fetcher (24h cache)
and is added explicitly. A parameter error injected once on the entries page re-walks from the
index and the day still fits.
"""

from __future__ import annotations

import argparse
import datetime

import pytest

from horseracing_scrape.jra.cli import run_command
from horseracing_scrape.jra.errors import JraParameterError
from horseracing_scrape.jra.models import JraResponse
from tests._jra_fake import FakeJraFetcher
from tests._seed_results176 import seed_from_results
from tests.jra_fixtures import jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
D_PREV = datetime.date(2026, 10, 31)
ROBOTS = 1
#: Kyoto cname prefix → (Tokyo prefix, fixture, rewrites)
_KYOTO = {
    "pw01drl0008": (
        "daylist_d_tokyo_20261101.sjis.html",
        (("pw01des0105", "pw01des0108"), ("東京", "京都")),
    ),
    "pw01des0108": ("entries_tokyo_20261101.sjis.html", ()),
    "pw01srl1008": (
        "daylist_s_tokyo_20261031.sjis.html",
        (("pw01ses1005", "pw01ses1008"), ("東京", "京都")),
    ),
    "pw01ses1008": ("results_tokyo_20261031.sjis.html", ()),
    "pw15ols0008": (
        "daylist_o_tokyo_20261101.sjis.html",
        (("pw151ouS305", "pw151ouS308"), ("東京", "京都")),
    ),
}


class TwoVenueFetcher(FakeJraFetcher):
    def __init__(self, *, fail_once: str | None = None):
        super().__init__()
        self._fail_once = fail_once

    def post(self, url, cname, *, kind):
        if self._fail_once and cname.startswith(self._fail_once):
            self._fail_once = None
            self.calls.append((url, cname))
            self.requests_sent += 1
            raise JraParameterError("injected once", url=url)
        for prefix, (name, rewrites) in _KYOTO.items():
            if cname.startswith(prefix):
                self.calls.append((url, cname))
                self.requests_sent += 1
                text = jra_text(name)
                for a, b in rewrites:
                    text = text.replace(a, b)
                return JraResponse(url, "x", 200, b"", text, self._now, kind, "k")
        return super().post(url, cname, kind=kind)


def _args(command, date, **over):
    base = dict(
        date=date.isoformat(),
        venue=None,
        race_ids=None,
        dry_run=False,
        json=True,
        database_url=None,
        policy=None,
        archive_dir=None,
        command=command,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _day(session, session_factory, fetcher_factory):
    seed_from_results(session)  # the 10/31 results need their entries
    sent = {}
    for command, date in (
        ("jra-entries", D),
        ("jra-odds", D),
        ("jra-results", D_PREV),
        ("jra-baba", D),
    ):
        f = fetcher_factory()
        r = run_command(
            command, _args(command, date), fetcher=f, session_factory=session_factory, date=date
        )
        assert r.exit_code in (0, 1), (command, r.to_json())
        sent[command] = f.requests_sent
    return sent


def test_one_day_fits_the_budget(session, session_factory):
    sent = _day(session, session_factory, TwoVenueFetcher)
    assert sent == {"jra-entries": 6, "jra-odds": 27, "jra-results": 5, "jra-baba": 3}
    without_odds = ROBOTS + sent["jra-entries"] + sent["jra-results"] + sent["jra-baba"]
    assert without_odds <= 20
    assert without_odds + sent["jra-odds"] <= 50


def test_one_rewalk_still_fits(session, session_factory):
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return TwoVenueFetcher(fail_once="pw01des0105" if calls["n"] == 1 else None)

    sent = _day(session, session_factory, factory)
    # index + day list + the failed page, then index + day list + page again (fresh cnames)
    assert sent["jra-entries"] == 9
    without_odds = ROBOTS + sent["jra-entries"] + sent["jra-results"] + sent["jra-baba"]
    assert without_odds <= 20 and without_odds + sent["jra-odds"] <= 50
