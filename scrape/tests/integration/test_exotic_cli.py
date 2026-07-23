"""T025 (US4): scrape-exotic-odds CLI ingests and reports a job summary."""

from __future__ import annotations

import datetime

import pytest
from bs4 import BeautifulSoup
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ExoticOdds, Horse, Race, RaceHorse, RaceResult
from sqlalchemy import func, select

from horseracing_scrape import cli, pipeline
from horseracing_scrape.fetch import FixtureFetcher
from horseracing_scrape.parse.results import parse_results
from tests.conftest import real_fixture

pytestmark = pytest.mark.integration

REAL_RESULT_RACE_ID = "202602011206"
REAL_RESULT_FIXTURE = "results_202602011206.html"


class CountingFixtureFetcher(FixtureFetcher):
    def __init__(self, pages):
        super().__init__(pages)
        self.calls: list[str] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.calls.append(url)
        return super().get(url, use_cache=use_cache)


def _seed_real_result_field(session, html: str) -> None:
    scraped = parse_results(html)
    session.merge(
        Race(
            race_id=REAL_RESULT_RACE_ID,
            race_number=6,
            race_date=datetime.date(2026, 7, 19),
            venue_code="02",
        )
    )
    for horse_number, row in enumerate(scraped.rows, start=1):
        horse_id = f"nk:{row.netkeiba_horse_id}"
        session.merge(Horse(horse_id=horse_id, horse_name=horse_id))
        session.add(
            RaceHorse(
                race_id=REAL_RESULT_RACE_ID,
                horse_id=horse_id,
                horse_number=horse_number,
                entry_status=EntryStatus.STARTED,
            )
        )
    session.commit()


def _race_result_count(session) -> int:
    return session.scalar(
        select(func.count())
        .select_from(RaceResult)
        .where(RaceResult.race_id == REAL_RESULT_RACE_ID)
    )


def _exotic_snapshot(session) -> list[tuple]:
    rows = session.execute(
        select(
            ExoticOdds.bet_type,
            ExoticOdds.selection,
            ExoticOdds.odds,
            ExoticOdds.coverage_scope,
            ExoticOdds.source,
        ).where(ExoticOdds.race_id == REAL_RESULT_RACE_ID)
    ).all()
    return sorted(
        (
            row.bet_type,
            tuple(row.selection),
            row.odds,
            row.coverage_scope,
            row.source,
        )
        for row in rows
    )


def test_scrape_exotic_odds_cli(session, tmp_path, capsys, database_url, monkeypatch):
    # standalone scrape-exotic-odds CLI on the REAL netkeiba result markup (Payout_Detail_Table).
    html = real_fixture(REAL_RESULT_FIXTURE)
    _seed_real_result_field(session, html)
    # stub the polite HttpFetcher with the saved real fixture (network-free)
    monkeypatch.setattr(cli, "HttpFetcher",
                        lambda **kw: FixtureFetcher({"u": html}))
    rc = cli.main(["scrape-exotic-odds", "--url", "u", "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "exotic_odds" in out and "status=succeeded" in out
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) > 0


def test_scrape_results_piggybacks_real_exotic_dividends(session):
    html = real_fixture(REAL_RESULT_FIXTURE)
    _seed_real_result_field(session, html)

    summary = pipeline.scrape_results(
        session,
        urls=["result"],
        fetcher=FixtureFetcher({"result": html}),
    )

    assert summary.status == "succeeded"
    assert _race_result_count(session) == 16
    assert len(_exotic_snapshot(session)) > 0


def test_scrape_results_reuses_each_fetched_html_for_exotics(session):
    html = real_fixture(REAL_RESULT_FIXTURE)
    _seed_real_result_field(session, html)
    urls = ["result-1", "result-2"]
    fetcher = CountingFixtureFetcher({url: html for url in urls})

    summary = pipeline.scrape_results(session, urls=urls, fetcher=fetcher)

    assert summary.status == "succeeded"
    assert fetcher.calls == urls
    assert len(fetcher.calls) == len(urls)


def test_scrape_results_exotic_piggyback_is_idempotent(session):
    html = real_fixture(REAL_RESULT_FIXTURE)
    _seed_real_result_field(session, html)

    pipeline.scrape_results(
        session,
        urls=["result"],
        fetcher=FixtureFetcher({"result": html}),
    )
    first = _exotic_snapshot(session)

    pipeline.scrape_results(
        session,
        urls=["result"],
        fetcher=FixtureFetcher({"result": html}),
    )
    second = _exotic_snapshot(session)

    assert len(first) > 0
    assert len(second) == len(first)
    assert second == first


def test_scrape_results_isolates_exotic_parse_failure(session, monkeypatch):
    html = real_fixture(REAL_RESULT_FIXTURE)
    _seed_real_result_field(session, html)

    def raise_exotic_error(_html: str):
        raise RuntimeError("broken payout table")

    monkeypatch.setattr(pipeline, "parse_exotic_odds", raise_exotic_error)
    summary = pipeline.scrape_results(
        session,
        urls=["result"],
        fetcher=FixtureFetcher({"result": html}),
    )

    assert summary.status != "failed"
    assert _race_result_count(session) == 16
    assert _exotic_snapshot(session) == []


def test_scrape_results_skips_html_without_payout_table(session):
    soup = BeautifulSoup(real_fixture(REAL_RESULT_FIXTURE), "lxml")
    for table in soup.select("table.Payout_Detail_Table"):
        table.decompose()
    html = str(soup)
    assert soup.select_one("table.RaceTable01") is not None
    assert soup.select_one("table.Payout_Detail_Table") is None
    _seed_real_result_field(session, html)

    summary = pipeline.scrape_results(
        session,
        urls=["result"],
        fetcher=FixtureFetcher({"result": html}),
    )

    assert summary.status != "failed"
    assert _race_result_count(session) == 16
    assert _exotic_snapshot(session) == []
