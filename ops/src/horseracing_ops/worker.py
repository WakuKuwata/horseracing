"""Resident worker that drains queued refresh jobs (Feature 024).

A job is claimed with ``SELECT … FOR UPDATE SKIP LOCKED`` so two workers never grab the same row;
we flip it to RUNNING and commit immediately (status, not a held lock, is the claim marker — so
scrape commits don't release a lock mid-run). On startup we recover stale RUNNING jobs (a crashed
worker): re-queue under max_retry, else mark FAILED. Operator-initiated only — NO scheduler/cron
(stays within the constitution's manual-execution scope).
"""

from __future__ import annotations

import datetime
import time
from concurrent.futures import ThreadPoolExecutor

from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from . import JOB_TYPE_DAY, JOB_TYPE_PREDICT, JOB_TYPE_RACE
from .config import CONFIG
from .deps import create_ops_engine
from .runner import make_fetcher, run_day, run_one, run_predict

#: job types the worker drains (refresh_day discovers + fans out; refresh_race scrapes; predict runs
#: the serving model for one race — Feature 028).
_CLAIMABLE = (JOB_TYPE_RACE, JOB_TYPE_DAY, JOB_TYPE_PREDICT)

#: a RUNNING job older than this (no progress) is presumed orphaned by a crashed worker.
STALE_RUNNING_SECONDS = CONFIG.stale_running_seconds
#: polling cadence for the daemon loop.
POLL_SECONDS = CONFIG.poll_seconds


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def recover_stale(session: Session, *, stale_seconds: int = STALE_RUNNING_SECONDS) -> int:
    """Re-queue (or fail) RUNNING jobs with no progress past the stale window. Returns count."""
    cutoff = _now() - datetime.timedelta(seconds=stale_seconds)
    stale = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type.in_(_CLAIMABLE))
        .where(IngestionJob.status == JobStatus.RUNNING)
        .where(IngestionJob.started_at.is_not(None))
        .where(IngestionJob.started_at < cutoff)
    ).all()
    for job in stale:
        if job.retry_count < job.max_retry:
            job.retry_count += 1
            job.status = JobStatus.QUEUED
            job.started_at = None
        else:
            job.status = JobStatus.FAILED
            job.completed_at = _now()
            job.error_message = "stale running job exceeded max_retry"
    session.commit()
    return len(stale)


def claim_one(session: Session) -> IngestionJob | None:
    """Atomically claim the oldest queued refresh job (FOR UPDATE SKIP LOCKED).

    Claims both refresh_day (discovery/fanout) and refresh_race (scrape). Oldest-first naturally
    processes a parent before the children it creates."""
    job = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type.in_(_CLAIMABLE))
        .where(IngestionJob.status == JobStatus.QUEUED)
        .order_by(IngestionJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = _now()
    session.commit()  # release the row lock; RUNNING is the claim marker
    return job


def _run_claimed(session: Session, job: IngestionJob, *, fetcher=None) -> None:
    if job.job_type == JOB_TYPE_DAY:
        runner = run_day
    elif job.job_type == JOB_TYPE_PREDICT:
        runner = run_predict
    else:
        runner = run_one
    try:
        runner(session, job, fetcher=fetcher)
    except Exception as exc:  # noqa: BLE001 — one bad job must not kill the worker
        session.rollback()
        if job.retry_count < job.max_retry:
            job.retry_count += 1
            job.status = JobStatus.QUEUED
            job.started_at = None
        else:
            job.status = JobStatus.FAILED
            job.completed_at = _now()
            job.error_message = str(exc)
        session.add(job)
        session.commit()


def drain(session: Session, *, fetcher=None, max_jobs: int | None = None) -> int:
    """Claim and run queued jobs until none remain (or max_jobs). Returns jobs processed."""
    n = 0
    while max_jobs is None or n < max_jobs:
        job = claim_one(session)
        if job is None:
            break
        _run_claimed(session, job, fetcher=fetcher)
        n += 1
    return n


def _worker_loop(factory: sessionmaker[Session], *, fetcher, max_jobs: int | None) -> int:
    with factory() as session:
        return drain(session, fetcher=fetcher, max_jobs=max_jobs)


def drain_concurrent(
    factory: sessionmaker[Session], *, max_workers: int, fetcher_factory=make_fetcher,
    max_jobs_per_worker: int | None = None,
) -> int:
    """Drain queued jobs with up to ``max_workers`` threads, each with its OWN session + fetcher.

    Safe because claim_one uses FOR UPDATE SKIP LOCKED — no two workers claim the same row. Caps the
    concurrent netkeiba load (FR-016), on top of HttpFetcher's per-domain rate-limit.
    """
    if max_workers <= 1:
        return _worker_loop(factory, fetcher=fetcher_factory(), max_jobs=max_jobs_per_worker)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_worker_loop, factory, fetcher=fetcher_factory(),
                        max_jobs=max_jobs_per_worker)
            for _ in range(max_workers)
        ]
        return sum(f.result() for f in futures)


def main() -> None:  # pragma: no cover — daemon entrypoint
    engine = create_ops_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        recover_stale(session)
    while True:
        processed = drain_concurrent(factory, max_workers=CONFIG.worker_concurrency)
        if processed == 0:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":  # pragma: no cover
    main()
