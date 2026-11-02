"""Seed a netkeiba-like pre-result state for the 2026-10-31 Tokyo results fixture (176 US2 tests).

The official results page is the only fixture for that day, so the "entries already in DB" state
is rebuilt from it: every listed horse becomes a started race_horses row (frame, 馬番, jockey,
trainer from the page), with a stored popularity deliberately different from the page so that
"the result page's popularity is never written" is observable."""

from __future__ import annotations

import datetime
from decimal import Decimal

from horseracing_db.enums import EntryStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, Trainer

from horseracing_scrape.jra.parse.results import parse_results_day
from tests.jra_fixtures import fetched_at, jra_text

RESULTS = "results_tokyo_20261031.sjis.html"
PAYOUTS = "payouts_tokyo_20261031.sjis.html"
D = datetime.date(2026, 10, 31)


def results_page(html: str | None = None, *, date: datetime.date = D):
    return parse_results_day(html or jra_text(RESULTS), expected_date=date)


def results_observed() -> datetime.datetime:
    return fetched_at(RESULTS)


def seed_from_results(session, page=None, *, race_ids=None, odds: Decimal | None = Decimal("9.9")):
    """races + race_horses (all started) + masters for every published race of the page."""
    page = page or results_page()
    for b in page.published():
        r = b.payload
        if race_ids is not None and r.race.race_id not in race_ids:
            continue
        rid = r.race.race_id
        session.merge(
            Race(
                race_id=rid,
                race_date=r.race.race_date,
                race_number=r.race.race_no,
                venue_code=r.race.venue_code,
                distance=r.race.distance,
                track_type=r.race.track_type,
            )
        )
        n = len(r.rows)
        for i, row in enumerate(r.rows, start=1):
            session.merge(Horse(horse_id=row.horse_id, horse_name=row.horse_name, birth_year=2022))
            session.merge(Jockey(jockey_id=row.jockey_id, jockey_name=row.jockey_name))
            session.merge(Trainer(trainer_id=row.trainer_id, trainer_name=row.trainer_name))
            session.merge(
                RaceHorse(
                    race_id=rid,
                    horse_id=row.horse_id,
                    horse_number=row.horse_number,
                    frame=row.frame,
                    jockey_id=row.jockey_id,
                    trainer_id=row.trainer_id,
                    jockey_weight=Decimal(str(row.jockey_weight)) if row.jockey_weight else None,
                    entry_status=EntryStatus.STARTED,
                    odds=odds,
                    # deliberately the reverse of any plausible page order
                    popularity=(n + 1 - i) if row.popularity is not None else None,
                )
            )
    session.commit()
    return page
