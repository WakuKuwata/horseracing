"""T012 (US1, A): enqueue -> worker -> terminal. A refresh does a FULL pass (entries+results+odds)."""

from __future__ import annotations

import pytest
from horseracing_db.models import RaceHorse, RaceResult
from sqlalchemy import func, select

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests._synth import mark_finished, seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def test_full_refresh_ingests_entries_results_odds(session, fixture_fetcher):
    seed_race(session, race_id=RID)
    job, reused = enqueue_race(session, RID)
    session.commit()
    assert reused is False

    processed = drain(session, fetcher=fixture_fetcher)
    assert processed == 1

    session.refresh(job)
    assert job.status == "succeeded"
    assert job.summary["kind"] == "entries+results+odds"   # one full pass
    # entries actually ingested (18-horse fixture), and results too
    n_horses = session.scalar(
        select(func.count()).select_from(RaceHorse).where(RaceHorse.race_id == RID))
    n_results = session.scalar(
        select(func.count()).select_from(RaceResult).where(RaceResult.race_id == RID))
    assert n_horses == 18 and n_results == 18


def test_results_insert_only_protects_jravan(session, fixture_fetcher):
    # a pre-existing (JRA-VAN) result row must survive the full refresh (INSERT-only).
    seed_race(session, race_id=RID)
    mark_finished(session, race_id=RID)  # seeds a seedH result row

    job, _ = enqueue_race(session, RID)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    session.refresh(job)
    assert job.summary["kind"] == "entries+results+odds"
    assert job.status in ("succeeded", "partial")
    kept = session.scalar(
        select(func.count()).select_from(RaceResult)
        .where(RaceResult.race_id == RID).where(RaceResult.horse_id == "seedH")
    )
    assert kept == 1  # seeded JRA-VAN result not destroyed
