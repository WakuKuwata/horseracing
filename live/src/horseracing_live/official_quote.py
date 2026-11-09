"""The JRA official 単複 page as a T−3 quote source (Feature 178 T064a; codex D24 Q2-1).

A production module both the capture (``near_post.capture_race``) and the isolation trial
(``source_trial``) import — the quote conversion and the walk live here, so neither depends on
the other. The walk is the 176 ``Walker`` (index → day page → this race's 単複 page, ``want``
limiting it to one race); the result is the 174 quote shape (馬番-keyed win odds + place range).
The official page carries no 人気: ``popularity`` is ``None`` and is never derived from odds here.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable

from horseracing_scrape.fetch import FetchError
from horseracing_scrape.models import ScrapedOdds, ScrapedOddsRow, ScrapedPlaceQuoteRow

QuoteFetcher = Callable[[str, datetime.date], ScrapedOdds]


def official_quote(odds) -> ScrapedOdds:
    """``JraWinOdds`` → ``ScrapedOdds`` (win rows for every numbered horse; place rows where the
    page gives a range)."""

    rows = tuple(
        ScrapedOddsRow(int(r.horse_number), r.win_odds, None)
        for r in odds.rows
        if r.horse_number is not None
    )
    place = tuple(
        ScrapedPlaceQuoteRow(int(r.horse_number), r.place_low, r.place_high, None)
        for r in odds.rows
        if r.horse_number is not None and (r.place_low is not None or r.place_high is not None)
    )
    return ScrapedOdds(key=None, rows=rows, place_rows=place, official_at=odds.official_at)


def fetch_official_quote(
    race_id: str,
    race_date: datetime.date,
    *,
    fetcher,
    walker_factory=None,
    parse=None,
) -> ScrapedOdds:
    """index → day page → this race's 単複 page. ``fetcher`` is a ``JraFetcher`` (``.post``)."""

    from horseracing_scrape.jra.navigate import Walker
    from horseracing_scrape.jra.parse.odds import parse_win_odds
    from horseracing_scrape.jra.race_odds import race_want

    walker_factory = walker_factory or (lambda f, d: Walker(f, date=d))
    parse = parse or parse_win_odds
    venue, want = race_want(race_id)
    for _day, _label, resp in walker_factory(fetcher, race_date).odds_pages(venue=venue, want=want):
        odds = parse(resp.text, expected_date=race_date)
        if odds.race_id != race_id:
            continue
        return official_quote(odds)
    raise FetchError(f"{race_id}: no 単複 page on the official site")


def make_quote_fetcher(fetcher, *, walker_factory=None, parse=None) -> QuoteFetcher:
    """Bind the walk to one fetcher (and, in tests, to a fake walker / parser)."""

    def _fetch(race_id: str, race_date: datetime.date) -> ScrapedOdds:
        return fetch_official_quote(
            race_id, race_date, fetcher=fetcher, walker_factory=walker_factory, parse=parse
        )

    return _fetch


def is_official_fetcher(fetcher) -> bool:
    """The official route POSTs cname forms; the netkeiba fetcher only GETs."""

    return callable(getattr(fetcher, "post", None))


__all__ = [
    "QuoteFetcher",
    "fetch_official_quote",
    "is_official_fetcher",
    "make_quote_fetcher",
    "official_quote",
]
