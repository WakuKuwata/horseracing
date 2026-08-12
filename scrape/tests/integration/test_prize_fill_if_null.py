"""092: 本賞金 lands from the entries page, and a later scrape can only fill it — never blank it.

`_upsert` overwrites every non-PK column, so wiring prize in naively would mean a single page
that stopped showing 本賞金 (or any parser refusal) NULLs out a good value on the next re-scrape.
prize_money is the active model's top-gain input, so that silent regression is the whole risk.
"""

from __future__ import annotations

import pytest
from horseracing_db.models import Race

from horseracing_scrape.pipeline import scrape_entries
from tests._synth import REAL_RID, real_entries_fetcher

pytestmark = pytest.mark.integration


def _scrape(session) -> None:
    fetcher, urls = real_entries_fetcher()
    summary = scrape_entries(session, urls=urls, fetcher=fetcher, complete_profiles_after=False)
    assert summary.status == "succeeded"


def test_prize_money_is_ingested_from_entries(session):
    _scrape(session)
    # fixture RaceData02: 本賞金:7000,2800,1800,1100,700万円 -> 1着 7000 (万円, JRA-VAN units)
    assert session.get(Race, REAL_RID).prize_money == 7000


def test_rescrape_is_idempotent_for_prize(session):
    _scrape(session)
    _scrape(session)
    assert session.get(Race, REAL_RID).prize_money == 7000


def test_a_page_without_prize_does_not_blank_an_existing_value(session):
    """The regression that would otherwise ship silently."""
    _scrape(session)
    assert session.get(Race, REAL_RID).prize_money == 7000

    fetcher, urls = real_entries_fetcher()
    original = fetcher.get  # serve the same page with 本賞金 removed
    fetcher.get = lambda url, **kw: original(url, **kw).replace("本賞金", "旧賞金")  # type: ignore[method-assign]
    assert scrape_entries(
        session, urls=urls, fetcher=fetcher, complete_profiles_after=False
    ).status == "succeeded"

    session.expire_all()
    assert session.get(Race, REAL_RID).prize_money == 7000, "must fill NULLs, never overwrite"


def test_prize_fills_a_null_left_by_an_earlier_scrape(session):
    """The forward-fill direction: a race ingested before this feature existed gets populated."""
    fetcher, urls = real_entries_fetcher()
    original = fetcher.get
    fetcher.get = lambda url, **kw: original(url, **kw).replace("本賞金", "旧賞金")  # type: ignore[method-assign]
    scrape_entries(session, urls=urls, fetcher=fetcher, complete_profiles_after=False)
    assert session.get(Race, REAL_RID).prize_money is None

    _scrape(session)
    session.expire_all()
    assert session.get(Race, REAL_RID).prize_money == 7000


def test_pedigree_failure_is_reported_as_an_error_not_silent_success(session):
    """A pedigree fetch that fails leaves `sire_id` NULL, so the horse re-enters the completion
    query and its ALREADY-SUCCESSFUL profile page is fetched again next pass. Reporting SUCCEEDED
    there hides a permanently repeating cost — at one request per minute that is not free."""
    from horseracing_scrape.fetch import FetchError
    from horseracing_scrape.pipeline import complete_profiles

    _scrape(session)  # creates nk: surrogate horses from the 18-horse fixture

    class _ProfileOkPedigreeBlocked:
        def get(self, url: str, *, use_cache: bool = True) -> str:
            if "/horse/ped/" in url:
                raise FetchError("pedigree blocked")
            raise FetchError("profile page not in fixtures")

    summary = complete_profiles(session, fetcher=_ProfileOkPedigreeBlocked(), limit=1)
    assert summary.errors > 0, "a failed fetch must be counted, not only described"
    assert summary.status != "succeeded"
