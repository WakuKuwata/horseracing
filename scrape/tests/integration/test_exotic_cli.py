"""T025 (US4): scrape-exotic-odds CLI ingests and reports a job summary."""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import ExoticOdds, Horse, Race, RaceHorse
from sqlalchemy import func, select

from horseracing_scrape import cli
from tests._synth import RACE_ID
from tests.conftest import fixture_html

pytestmark = pytest.mark.integration


def _seed(session):
    session.merge(Race(race_id=RACE_ID, race_number=11, race_date=datetime.date(2025, 6, 1),
                       venue_code="05"))
    for i in (1, 2, 3):
        session.merge(Horse(horse_id=f"H{i}", horse_name=f"H{i}"))
        session.add(RaceHorse(race_id=RACE_ID, horse_id=f"H{i}", horse_number=i,
                              entry_status=EntryStatus.STARTED))
    session.commit()


def test_scrape_exotic_odds_cli(session, tmp_path, capsys, database_url, monkeypatch):
    _seed(session)
    # stub the polite HttpFetcher with the saved fixture (network-free)
    from horseracing_scrape.fetch import FixtureFetcher
    monkeypatch.setattr(cli, "HttpFetcher",
                        lambda **kw: FixtureFetcher({"u": fixture_html("exotic_odds")}))
    rc = cli.main(["scrape-exotic-odds", "--url", "u", "--database-url", database_url])
    assert rc == 0
    out = capsys.readouterr().out
    assert "exotic_odds" in out and "status=succeeded" in out
    assert session.scalar(select(func.count()).select_from(ExoticOdds)) > 0
