"""Lane split + interactive-first claim order (interactive-latency fix).

Before the split every job type shared one FIFO, so a day refresh fanout parked interactive
clicks behind refresh_race×36 + predict×36 (measured predict manual_ui wait p95 ≈ 17min for a
~40s run). These tests pin the scheduling contract: lane filtering, the CPU-lane priority CASE
(manual > auto_after_predict > backlog), and IO-lane FIFO preservation.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import JobStatus

from horseracing_ops.enqueue import (
    enqueue_expected_return,
    enqueue_predict,
    enqueue_race,
    enqueue_recommend,
    enqueue_refresh_range,
)
from horseracing_ops.worker import _CPU_LANE, _IO_LANE, claim_one
from tests._synth import seed_race

pytestmark = pytest.mark.integration

# distinct race ids so the enqueue dedup (per-race advisory lock) never merges the jobs
R1, R2, R3, R4, R5 = ("202406050901", "202406050902", "202406050903", "202406050904",
                      "202406050905")


def test_cpu_lane_claims_interactive_before_backlog(session):
    """Enqueue order = backlog first; claim order = interactive first, batch stays FIFO.

    Codex blocker pinned here: a batch (auto_after_refresh) follow-up recommend must NOT
    overtake the batch's remaining predicts — the legacy two-gamma fit reads whatever
    predictions are persisted at recommend time, so batch relative order is part of the
    numeric contract. Only the MANUAL unit (predict + its follow-up recommend) jumps."""
    for rid in (R1, R2, R3, R4, R5):
        seed_race(session, race_id=rid)
    # oldest → newest: auto predict backlog, auto batch follow-up rec, manual predict,
    # manual-origin follow-up rec, manual rec button
    # per-enqueue commit: created_at is the TRANSACTION timestamp (server_default now()), and
    # the production enqueues whose order matters (clicks, follow-ups) are separate transactions
    auto_pred, _ = enqueue_predict(session, R1, origin="auto_after_refresh")
    session.commit()
    auto_rec, _ = enqueue_recommend(session, R2, source="auto_after_predict")
    session.commit()
    man_pred, _ = enqueue_predict(session, R3, origin="manual_ui")
    session.commit()
    man_followup, _ = enqueue_recommend(
        session, R5, source="auto_after_predict", predict_origin="manual_ui"
    )
    session.commit()
    man_rec, _ = enqueue_recommend(session, R4, source="manual")
    session.commit()

    claimed = []
    for _ in range(5):
        job = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
        assert job is not None
        claimed.append(job.ingestion_job_id)
    assert claim_one(session, job_types=_CPU_LANE, interactive_first=True) is None

    # rank 0 (manual + manual-origin follow-up, FIFO between them) → rank 1 (batch, FIFO:
    # the batch recommend keeps its created_at place AFTER the batch predict)
    assert claimed == [
        man_pred.ingestion_job_id, man_followup.ingestion_job_id, man_rec.ingestion_job_id,
        auto_pred.ingestion_job_id, auto_rec.ingestion_job_id,
    ]


def test_manual_click_promotes_queued_auto_recommend(session):
    """A manual 買い目生成 click on a QUEUED batch recommend promotes it to rank 0 (codex)."""
    seed_race(session, race_id=R1)
    auto_rec, _ = enqueue_recommend(session, R1, source="auto_after_predict")
    session.commit()
    promoted, reused = enqueue_recommend(session, R1, source="manual")
    session.commit()
    assert reused and promoted.ingestion_job_id == auto_rec.ingestion_job_id
    assert (promoted.summary or {}).get("source") == "manual"


def test_only_one_refresh_range_runs_at_a_time(session):
    """A RUNNING refresh_range hides other queued ranges from the claim (bulk cap, codex);
    single-race jobs stay claimable."""
    seed_race(session, race_id=R1)
    range_a, _ = enqueue_refresh_range(
        session, date_from=datetime.date(2024, 12, 27), date_to=datetime.date(2024, 12, 27)
    )
    session.commit()
    range_b, _ = enqueue_refresh_range(
        session, date_from=datetime.date(2024, 12, 28), date_to=datetime.date(2024, 12, 28)
    )
    session.commit()
    predict, _ = enqueue_predict(session, R1, origin="auto_after_refresh")
    session.commit()

    first = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert first.ingestion_job_id == range_a.ingestion_job_id  # FIFO within rank 1
    # range_a is RUNNING -> range_b is hidden; the predict is still claimable
    second = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert second.ingestion_job_id == predict.ingestion_job_id
    assert claim_one(session, job_types=_CPU_LANE, interactive_first=True) is None


def test_lanes_do_not_claim_each_others_jobs(session):
    seed_race(session, race_id=R1)
    seed_race(session, race_id=R2)
    refresh, _ = enqueue_race(session, R1, origin="manual_ui")
    predict, _ = enqueue_predict(session, R2, origin="manual_ui")
    session.commit()

    # refresh_range is CPU-lane bulk (its payload is the live CLI = predict+recommend backfill,
    # no scrape) — claimed AFTER the interactive predict (rank 2 vs rank 0).
    rrange, _ = enqueue_refresh_range(
        session, date_from=datetime.date(2024, 12, 28), date_to=datetime.date(2024, 12, 28)
    )
    session.commit()

    cpu_job = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert cpu_job is not None and cpu_job.ingestion_job_id == predict.ingestion_job_id
    bulk_job = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert bulk_job is not None and bulk_job.ingestion_job_id == rrange.ingestion_job_id
    # CPU lane exhausted: the refresh job is invisible to it
    assert claim_one(session, job_types=_CPU_LANE, interactive_first=True) is None

    io_job = claim_one(session, job_types=_IO_LANE)
    assert io_job is not None and io_job.ingestion_job_id == refresh.ingestion_job_id
    assert claim_one(session, job_types=_IO_LANE) is None


def test_expected_return_is_cpu_lane_only(session):
    """Feature 137: the recompute is a training-CLI subprocess with no scrape — it must never hold
    one of the politeness-capped IO slots."""
    seed_race(session, race_id=R1)
    refresh, _ = enqueue_race(session, R1, origin="daily_bulk")
    er, _ = enqueue_expected_return(
        session, datetime.date(2024, 12, 28), source="auto_after_refresh"
    )
    session.commit()

    io_job = claim_one(session, job_types=_IO_LANE)
    assert io_job is not None and io_job.ingestion_job_id == refresh.ingestion_job_id
    assert claim_one(session, job_types=_IO_LANE) is None  # the recompute is invisible to IO
    cpu_job = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert cpu_job is not None and cpu_job.ingestion_job_id == er.ingestion_job_id


def test_only_one_expected_return_runs_at_a_time(session):
    """enqueue reuses only QUEUED jobs, so a newer job for a date can exist while an older one is
    RUNNING. Running both would have two processes replacing the same races' rows — the claim
    hides every queued expected_return while one runs; other CPU work stays claimable."""
    seed_race(session, race_id=R1)
    day = datetime.date(2024, 12, 28)
    first, _ = enqueue_expected_return(session, day, source="auto_after_refresh")
    session.commit()
    running = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert running.ingestion_job_id == first.ingestion_job_id

    newer, reused = enqueue_expected_return(session, day, source="auto_after_refresh")
    session.commit()
    other_day, _ = enqueue_expected_return(
        session, datetime.date(2024, 12, 29), source="manual_ui"
    )
    session.commit()
    predict, _ = enqueue_predict(session, R1, origin="auto_after_refresh")
    session.commit()
    assert reused is False

    nxt = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert nxt.ingestion_job_id == predict.ingestion_job_id  # both recomputes are held back
    assert claim_one(session, job_types=_CPU_LANE, interactive_first=True) is None

    running.status = JobStatus.SUCCEEDED
    session.add(running)
    session.commit()
    # the cap lifts; the manual (rank 0) recompute goes first, then the older auto one
    a = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert a.ingestion_job_id == other_day.ingestion_job_id
    assert claim_one(session, job_types=_CPU_LANE, interactive_first=True) is None
    a.status = JobStatus.SUCCEEDED
    session.add(a)
    session.commit()
    b = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert b.ingestion_job_id == newer.ingestion_job_id


def test_manual_expected_return_ranks_interactive(session):
    """A recompute triggered by a race-page refresh click overtakes the batch backlog; one from a
    bulk refresh stays FIFO behind older batch work."""
    seed_race(session, race_id=R1)
    backlog, _ = enqueue_predict(session, R1, origin="auto_after_refresh")
    session.commit()
    auto_er, _ = enqueue_expected_return(
        session, datetime.date(2024, 12, 27), source="auto_after_refresh"
    )
    session.commit()

    first = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert first.ingestion_job_id == backlog.ingestion_job_id  # FIFO within rank 1
    first.status = JobStatus.QUEUED  # put it back: only the ORDER is under test here
    first.started_at = None
    session.add(first)
    session.commit()

    manual_er, _ = enqueue_expected_return(
        session, datetime.date(2024, 12, 28), source="manual_ui"
    )
    session.commit()
    top = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert top.ingestion_job_id == manual_er.ingestion_job_id
    rest = claim_one(session, job_types=_CPU_LANE, interactive_first=True)
    assert rest.ingestion_job_id == backlog.ingestion_job_id
    # auto_er is still queued behind the RUNNING manual recompute (one-at-a-time cap)
    assert claim_one(session, job_types=_CPU_LANE, interactive_first=True) is None
    assert auto_er.ingestion_job_id not in {top.ingestion_job_id, rest.ingestion_job_id}


def test_io_lane_stays_fifo(session):
    """Parent-before-children ordering depends on oldest-first — pin it."""
    seed_race(session, race_id=R1)
    seed_race(session, race_id=R2)
    first, _ = enqueue_race(session, R1, origin="daily_bulk")
    session.commit()
    second, _ = enqueue_race(session, R2, origin="daily_bulk")
    session.commit()

    a = claim_one(session, job_types=_IO_LANE)
    b = claim_one(session, job_types=_IO_LANE)
    assert [a.ingestion_job_id, b.ingestion_job_id] == [
        first.ingestion_job_id, second.ingestion_job_id,
    ]


def test_default_claim_is_unchanged(session):
    """No args → all claimable types, oldest-first (backward compat for drain/drain_concurrent)."""
    seed_race(session, race_id=R1)
    seed_race(session, race_id=R2)
    refresh, _ = enqueue_race(session, R1, origin="daily_bulk")
    session.commit()
    predict, _ = enqueue_predict(session, R2, origin="manual_ui")
    session.commit()

    a = claim_one(session)
    b = claim_one(session)
    assert [a.ingestion_job_id, b.ingestion_job_id] == [
        refresh.ingestion_job_id, predict.ingestion_job_id,
    ]
    assert a.status == JobStatus.RUNNING and b.status == JobStatus.RUNNING


def test_lane_daemon_repolls_after_empty_pass(session, engine, monkeypatch):
    """A CPU lane daemon whose queue starts EMPTY must re-poll and pick up a job enqueued later
    (a one-shot drain thread would exit and strand the follow-up — codex structural risk)."""
    from sqlalchemy.orm import sessionmaker

    from horseracing_ops import worker as worker_mod

    ran: list[str] = []

    def fake_run_claimed(sess, job, *, fetcher=None):
        ran.append(job.job_type)
        job.status = JobStatus.SUCCEEDED
        sess.add(job)
        sess.commit()

    monkeypatch.setattr(worker_mod, "_run_claimed", fake_run_claimed)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    # iteration 1: empty queue (processed 0). Enqueue between iterations via idle_sleep hook —
    # simplest deterministic simulation: enqueue AFTER constructing the daemon args but before
    # the second iteration by running two bounded iterations around an enqueue.
    total_first = worker_mod._lane_daemon(
        factory, job_types=worker_mod._CPU_LANE, interactive_first=True,
        fetcher_factory=None, max_iterations=1, idle_sleep=0,
    )
    assert total_first == 0 and ran == []

    seed_race(session, race_id=R1)
    enqueue_predict(session, R1, origin="auto_after_refresh")
    session.commit()

    total_second = worker_mod._lane_daemon(
        factory, job_types=worker_mod._CPU_LANE, interactive_first=True,
        fetcher_factory=None, max_iterations=1, idle_sleep=0,
    )
    assert total_second == 1 and ran == ["predict"]


def test_recover_stale_uses_range_window(session):
    """A RUNNING refresh_range inside the 3600s subprocess window must NOT be re-queued by the
    generic 900s stale sweep; a stale single-race job still is (codex)."""
    import datetime as dt

    from horseracing_ops.worker import recover_stale

    seed_race(session, race_id=R1)
    old = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1800)
    rrange, _ = enqueue_refresh_range(
        session, date_from=datetime.date(2024, 12, 28), date_to=datetime.date(2024, 12, 28)
    )
    predict, _ = enqueue_predict(session, R1, origin="auto_after_refresh")
    session.commit()
    for j in (rrange, predict):
        j.status = JobStatus.RUNNING
        j.started_at = old
    session.commit()

    n = recover_stale(session)
    session.expire_all()
    assert n == 1
    assert session.get(type(predict), predict.ingestion_job_id).status == JobStatus.QUEUED
    assert session.get(type(rrange), rrange.ingestion_job_id).status == JobStatus.RUNNING
