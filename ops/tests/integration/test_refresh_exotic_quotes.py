"""Daily refresh captures the PRE-RACE exotic price grid — and only while it is still pre-race.

These prices are the only thing that can drive combination selection: `exotic_odds` holds the
DIVIDEND, which exists solely for the combination that came in and is knowable only after the race.
They are also unrecoverable — a race that runs uncaptured is lost permanently — so the capture has
to happen on the ordinary daily pass, not on a separate command someone remembers to run.

The gate is what keeps that affordable. Each bet type is one extra request per race, and a bulk day
is mostly finished races whose grids are worth nothing for selection. Fetching them would multiply
the day's volume for no gain, so a settled race must issue ZERO extra requests.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from horseracing_db.models import ExoticQuote
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.urls import entries_url, exotic_quotes_url, result_url, win_odds_url
from sqlalchemy import select

from horseracing_ops import runner
from horseracing_ops.config import CONFIG
from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests._synth import mark_finished, seed_race
from tests.conftest import _read  # same fixture loader the shared fetcher uses

pytestmark = pytest.mark.integration

RID = "202406050911"
QUOTE_TYPES = ("quinella", "wide", "trio")
#: captured from a real race; the parser takes race_id as an argument and does not cross-check it,
#: so one grid per bet type is enough to exercise the whole path.
_QUOTE_FIXTURES = {
    "quinella": "exotic_quotes_type4_202601010201.json",
    "wide": "exotic_quotes_type5_202601010201.json",
    "trio": "exotic_quotes_type7_202601010201.json",
}


class CountingFixtureFetcher(FixtureFetcher):
    """Records every URL so a test can assert what was NOT requested."""

    def __init__(self, pages):
        super().__init__(pages)
        self.calls: list[str] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.calls.append(url)
        return super().get(url, use_cache=use_cache)


def _pages(*, with_results: bool) -> dict[str, str]:
    pages = {
        entries_url(RID): _read(f"entries_{RID}.html"),
        win_odds_url(RID): _read(f"odds_{RID}.json"),
    }
    if with_results:
        pages[result_url(RID)] = _read(f"results_{RID}.html")
    for bt, name in _QUOTE_FIXTURES.items():
        pages[exotic_quotes_url(RID, bt)] = _read(name)
    return pages


def _quotes(session, race_id):
    return {q.bet_type: q for q in session.scalars(
        select(ExoticQuote).where(ExoticQuote.race_id == race_id)
    )}


def test_pending_race_captures_the_grid_for_every_configured_bet_type(session):
    """No result page in the fixture set → the race stays pending → the grid is captured."""
    seed_race(session, race_id=RID)
    job, _ = enqueue_race(session, RID, origin="daily_bulk")
    session.commit()
    fetcher = CountingFixtureFetcher(_pages(with_results=False))

    drain(session, fetcher=fetcher, max_jobs=1)

    got = _quotes(session, RID)
    assert set(got) == set(QUOTE_TYPES)
    for bt, row in got.items():
        assert row.n_combinations > 0, bt
        assert row.observed_at is not None
        assert row.official_at is not None, "the source-declared observation time must survive"
        # the grid is the whole price table, losing combinations included
        assert len(row.quotes) == row.n_combinations


def test_settled_race_issues_no_extra_requests(session):
    """The volume gate. A finished race's grid cannot drive selection (we have its dividends), so
    capturing it would cost 3 requests per race on a bulk day for nothing."""
    seed_race(session, race_id=RID)
    mark_finished(session, race_id=RID)
    job, _ = enqueue_race(session, RID, origin="daily_bulk")
    session.commit()
    fetcher = CountingFixtureFetcher(_pages(with_results=True))

    drain(session, fetcher=fetcher, max_jobs=1)

    quote_urls = [u for u in fetcher.calls if "type=4" in u or "type=5" in u or "type=7" in u]
    assert quote_urls == [], f"settled race fetched exotic grids: {quote_urls}"
    assert _quotes(session, RID) == {}


def test_capture_can_be_disabled_by_configuration(session, monkeypatch):
    """Empty bet-type list = off. The dial has to work without a code change, because it is the
    only lever on the added request volume."""
    # OpsConfig is frozen (values are read once at import), so swap the module-level object the
    # runner reads rather than mutating the dataclass.
    monkeypatch.setattr(runner, "CONFIG", replace(CONFIG, exotic_quote_bet_types=()))
    seed_race(session, race_id=RID)
    job, _ = enqueue_race(session, RID, origin="daily_bulk")
    session.commit()
    fetcher = CountingFixtureFetcher(_pages(with_results=False))

    drain(session, fetcher=fetcher, max_jobs=1)

    assert _quotes(session, RID) == {}
    assert not [u for u in fetcher.calls if "type=4" in u]


def test_a_failing_grid_does_not_break_the_refresh(session):
    """One unavailable bet type must not cost the entries/odds work that already succeeded."""
    seed_race(session, race_id=RID)
    job, _ = enqueue_race(session, RID, origin="daily_bulk")
    session.commit()
    pages = _pages(with_results=False)
    del pages[exotic_quotes_url(RID, "trio")]          # simulate one refused/absent grid
    fetcher = CountingFixtureFetcher(pages)

    drain(session, fetcher=fetcher, max_jobs=1)

    session.refresh(job)
    assert job.status in ("succeeded", "partial"), job.status
    got = _quotes(session, RID)
    assert set(got) == {"quinella", "wide"}, "the healthy grids must still land"


def test_recapture_overwrites_in_place(session):
    """Single latest value per (race, bet type) — constitution V. A second refresh of a still-
    pending race must update the row, not accumulate a history of grids."""
    seed_race(session, race_id=RID)
    for _ in range(2):
        job, _ = enqueue_race(session, RID, origin="daily_bulk", force=True)
        session.commit()
        drain(session, fetcher=CountingFixtureFetcher(_pages(with_results=False)), max_jobs=1)

    rows = session.scalars(select(ExoticQuote).where(ExoticQuote.race_id == RID)).all()
    assert len(rows) == len(QUOTE_TYPES), f"expected one row per bet type, got {len(rows)}"


def test_race_that_finishes_within_the_same_job_is_skipped(session):
    """The gate reads the freshest state, not the state at job start.

    A race with no results in the DB may already have run — the refresh's own results sub-step is
    what discovers that. Evaluating the gate AFTER that step means such a race costs zero grid
    requests, which is the common case whenever ingest is behind. (Observed on real data: a race
    with no stored results had its results land mid-job, and no grid was fetched.)
    """
    seed_race(session, race_id=RID)                     # pending as far as the DB knows
    job, _ = enqueue_race(session, RID, origin="daily_bulk")
    session.commit()
    fetcher = CountingFixtureFetcher(_pages(with_results=True))   # ...but it already ran

    drain(session, fetcher=fetcher, max_jobs=1)

    assert _quotes(session, RID) == {}
    assert not [u for u in fetcher.calls if "type=" in u and "type=1" not in u]
