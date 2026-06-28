"""T012 (US1): enqueue -> worker -> terminal, with kind decided at run time from result-pending."""

from __future__ import annotations

import pytest
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import func, select

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests._synth import mark_finished, seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_pending_race_fetches_entries_and_odds(session, fixture_fetcher):
    seed_race(session, race_id=RID)  # no race_results -> pending
    job, reused = enqueue_race(session, RID)
    session.commit()
    assert reused is False

    processed = drain(session, fetcher=fixture_fetcher)
    assert processed == 1

    session.refresh(job)
    assert job.status == "succeeded"
    assert job.summary["kind"] == "entries+odds"
    # entries actually ingested (18-horse fixture)
    n = session.scalar(select(func.count()).select_from(RaceHorse).where(RaceHorse.race_id == RID))
    assert n == 18


def test_finished_race_fetches_results(session, fixture_fetcher):
    # realistic order: entries first (pending), then the race runs and results land.
    seed_race(session, race_id=RID)
    first, _ = enqueue_race(session, RID)
    session.commit()
    drain(session, fetcher=fixture_fetcher)  # entries+odds while pending
    session.refresh(first)
    assert first.summary["kind"] == "entries+odds"

    mark_finished(session, race_id=RID)  # race_results present -> no longer pending
    job, _ = enqueue_race(session, RID, force=True)  # force past freshness
    session.commit()

    drain(session, fetcher=fixture_fetcher)
    session.refresh(job)
    assert job.summary["kind"] == "results"
    assert job.status in ("succeeded", "partial")
    # the seeded JRA-VAN result row is not destroyed (INSERT-only protection)
    kept = session.scalar(
        select(func.count()).select_from(RaceResult)
        .where(RaceResult.race_id == RID).where(RaceResult.horse_id == "seedH")
    )
    assert kept == 1
