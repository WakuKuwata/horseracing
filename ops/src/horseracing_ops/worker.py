"""Resident worker that drains queued refresh jobs (Feature 024).

A job is claimed with ``SELECT … FOR UPDATE SKIP LOCKED`` so two workers never grab the same row;
we flip it to RUNNING and commit immediately (status, not a held lock, is the claim marker — so
scrape commits don't release a lock mid-run). On startup we recover stale RUNNING jobs (a crashed
worker): re-queue under max_retry, else mark FAILED. Operator-initiated only — NO scheduler/cron
(stays within the constitution's manual-execution scope).
"""

from __future__ import annotations

import datetime
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Thread

from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from sqlalchemy import and_, case, or_, select
from sqlalchemy.orm import Session, sessionmaker

from . import (
    JOB_TYPE_DAY,
    JOB_TYPE_PREDICT,
    JOB_TYPE_RACE,
    JOB_TYPE_RECOMMEND,
    JOB_TYPE_REFRESH_RANGE,
)
from .config import CONFIG
from .deps import create_ops_engine
from .runner import (
    make_fetcher,
    run_day,
    run_one,
    run_predict,
    run_recommend,
    run_refresh_range,
)

#: job types the worker drains (refresh_day discovers + fans out; refresh_race scrapes; predict runs
#: the serving model for one race — Feature 028; recommend generates buy-ups — Feature 043).
_CLAIMABLE = (JOB_TYPE_RACE, JOB_TYPE_DAY, JOB_TYPE_PREDICT, JOB_TYPE_RECOMMEND,
              JOB_TYPE_REFRESH_RANGE)

#: Lane split (interactive latency): scrape-bound jobs (netkeiba politeness caps their
#: concurrency) vs compute-bound jobs (subprocesses that never touch netkeiba). Before the split
#: every job type shared one FIFO, so a day refresh fanning out 36 races parked interactive
#: clicks behind refresh_race×36 + predict×36 (measured predict manual_ui wait p95 ≈ 17min for a
#: ~40s run). Lanes keep the IO queue's parent-before-children FIFO while the CPU lane serves
#: interactive work first. refresh_range is CPU, not IO (codex review): its payload is the live
#: CLI = predict+recommend backfill with NO scrape, so in the IO lane it would both hog a
#: politeness slot it doesn't need and run heavy compute outside the CPU lane's memory budget.
_IO_LANE = (JOB_TYPE_RACE, JOB_TYPE_DAY)
_CPU_LANE = (JOB_TYPE_PREDICT, JOB_TYPE_RECOMMEND, JOB_TYPE_REFRESH_RANGE)


def _interactive_rank():
    """CPU-lane claim order: user-facing work first, batch work in enqueue (FIFO) order.

    rank 0 — a human is waiting: predict from the 予測 button (predict_origin=manual_ui),
             recommend from the 買い目生成 button (source=manual), or the follow-up recommend of
             a MANUAL predict (source=auto_after_predict + predict_origin=manual_ui — the user's
             clicked unit completes without a second wait).
    rank 1 — everything else, created_at FIFO. Deliberately NOT "recommend before backlog
             predicts" (codex blocker): the legacy two-gamma fit reads the predictions persisted
             at recommend time, so hoisting a batch recommend ahead of the batch's remaining
             predicts would change its fit inputs vs the historical FIFO. created_at order keeps
             the batch's predicts ahead of their follow-up recommends (a follow-up is created only
             when its predict completes), preserving the old all-predicts-first shape.
    Starvation of rank 1 by rank 0 is acceptable: interactive clicks are rare and short-lived.
    The provenance keys live in summary JSONB (NOT the ingestion_jobs.source column, which is
    the data source label): predict → summary->>'predict_origin', recommend → summary->>'source'
    (+ propagated 'predict_origin' on follow-ups).
    """
    origin = IngestionJob.summary.op("->>")("predict_origin")
    src = IngestionJob.summary.op("->>")("source")
    return case(
        (and_(IngestionJob.job_type == JOB_TYPE_PREDICT, origin == "manual_ui"), 0),
        (and_(IngestionJob.job_type == JOB_TYPE_RECOMMEND, src == "manual"), 0),
        (and_(IngestionJob.job_type == JOB_TYPE_RECOMMEND,
              src == "auto_after_predict", origin == "manual_ui"), 0),
        else_=1,
    )

#: a RUNNING job older than this (no progress) is presumed orphaned by a crashed worker.
STALE_RUNNING_SECONDS = CONFIG.stale_running_seconds
#: polling cadence for the daemon loop.
POLL_SECONDS = CONFIG.poll_seconds
#: after an unexpected loop error (e.g. the DB connection dropped across a laptop sleep) back off
#: this long before retrying, so a persistent outage doesn't become a hot retry loop.
ERROR_BACKOFF_SECONDS = 5.0


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


#: refresh_range holds its live subprocess up to _LIVE_TIMEOUT_S=3600s, so the generic 900s stale
#: window would re-queue a legitimately RUNNING range on worker restart while its (orphaned)
#: subprocess may still be writing (codex). Margin over the subprocess timeout, not under it.
STALE_RANGE_SECONDS = 3900


def recover_stale(session: Session, *, stale_seconds: int = STALE_RUNNING_SECONDS) -> int:
    """Re-queue (or fail) RUNNING jobs with no progress past the stale window. Returns count.

    Per-type window: refresh_range uses STALE_RANGE_SECONDS (≥ its subprocess timeout)."""
    now = _now()
    cutoff = now - datetime.timedelta(seconds=stale_seconds)
    range_cutoff = now - datetime.timedelta(seconds=max(stale_seconds, STALE_RANGE_SECONDS))
    stale = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type.in_(_CLAIMABLE))
        .where(IngestionJob.status == JobStatus.RUNNING)
        .where(IngestionJob.started_at.is_not(None))
        .where(
            or_(
                and_(IngestionJob.job_type != JOB_TYPE_REFRESH_RANGE,
                     IngestionJob.started_at < cutoff),
                and_(IngestionJob.job_type == JOB_TYPE_REFRESH_RANGE,
                     IngestionJob.started_at < range_cutoff),
            )
        )
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


def claim_one(
    session: Session,
    *,
    job_types: tuple[str, ...] = _CLAIMABLE,
    interactive_first: bool = False,
) -> IngestionJob | None:
    """Atomically claim the next queued job of ``job_types`` (FOR UPDATE SKIP LOCKED).

    Default order is oldest-first, which naturally processes a refresh_day parent before the
    children it creates. ``interactive_first`` (CPU lane) prepends the ``_interactive_rank``
    CASE so a fresh manual click overtakes a queued backlog on the very next claim.
    ``ingestion_job_id`` is the final tie-break: created_at can collide inside one fanout
    transaction, and an unordered tie would make the claim order nondeterministic (codex).

    refresh_range cap (codex): at most ONE refresh_range runs at a time — each spawns a live
    subprocess doing a whole range's predict+recommend backfill, so two would blow the CPU
    lane's memory budget while starving single-race jobs. Best-effort (two threads can race the
    NOT-EXISTS check within one poll tick), which only ever degrades to the pre-cap behavior."""
    stmt = (
        select(IngestionJob)
        .where(IngestionJob.job_type.in_(job_types))
        .where(IngestionJob.status == JobStatus.QUEUED)
    )
    if JOB_TYPE_REFRESH_RANGE in job_types:
        running_range = (
            select(IngestionJob.ingestion_job_id)
            .where(IngestionJob.job_type == JOB_TYPE_REFRESH_RANGE)
            .where(IngestionJob.status == JobStatus.RUNNING)
            .exists()
        )
        stmt = stmt.where(
            or_(IngestionJob.job_type != JOB_TYPE_REFRESH_RANGE, ~running_range)
        )
    order = (
        (_interactive_rank(), IngestionJob.created_at.asc(), IngestionJob.ingestion_job_id.asc())
        if interactive_first
        else (IngestionJob.created_at.asc(), IngestionJob.ingestion_job_id.asc())
    )
    job = session.scalars(
        stmt.order_by(*order).with_for_update(skip_locked=True).limit(1)
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
    elif job.job_type == JOB_TYPE_RECOMMEND:
        runner = run_recommend
    elif job.job_type == JOB_TYPE_REFRESH_RANGE:
        runner = run_refresh_range
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


def drain(
    session: Session,
    *,
    fetcher=None,
    max_jobs: int | None = None,
    job_types: tuple[str, ...] = _CLAIMABLE,
    interactive_first: bool = False,
) -> int:
    """Claim and run queued jobs until none remain (or max_jobs). Returns jobs processed."""
    n = 0
    while max_jobs is None or n < max_jobs:
        job = claim_one(session, job_types=job_types, interactive_first=interactive_first)
        if job is None:
            break
        _run_claimed(session, job, fetcher=fetcher)
        n += 1
    return n


def _worker_loop(
    factory: sessionmaker[Session],
    *,
    fetcher,
    max_jobs: int | None,
    job_types: tuple[str, ...] = _CLAIMABLE,
    interactive_first: bool = False,
) -> int:
    with factory() as session:
        return drain(
            session, fetcher=fetcher, max_jobs=max_jobs,
            job_types=job_types, interactive_first=interactive_first,
        )


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


def drain_lanes(
    factory: sessionmaker[Session], *, io_workers: int, cpu_workers: int,
    fetcher_factory=make_fetcher, max_jobs_per_worker: int | None = None,
) -> int:
    """ONE drain pass with both lanes running simultaneously (test/one-shot helper).

    IO lane: refresh jobs, oldest-first (parent-before-children), each thread with its own polite
    fetcher (netkeiba concurrency stays capped by ``io_workers`` exactly as before). CPU lane:
    predict/recommend, interactive-first; ``fetcher=None`` because these runners never scrape
    (run_predict/run_recommend take the kwarg for the uniform runner signature and ignore it).

    Caveat: this is a barrier — a CPU thread that finds its queue empty exits even while an IO
    thread is still enqueuing predict follow-ups. The daemon therefore does NOT loop on this; it
    runs persistent per-lane threads (``_lane_daemon``) so each lane re-polls independently."""
    with ThreadPoolExecutor(max_workers=io_workers + cpu_workers) as pool:
        futures = [
            pool.submit(_worker_loop, factory, fetcher=fetcher_factory(),
                        max_jobs=max_jobs_per_worker, job_types=_IO_LANE)
            for _ in range(io_workers)
        ] + [
            pool.submit(_worker_loop, factory, fetcher=None,
                        max_jobs=max_jobs_per_worker, job_types=_CPU_LANE,
                        interactive_first=True)
            for _ in range(cpu_workers)
        ]
        return sum(f.result() for f in futures)


def _lane_daemon(
    factory: sessionmaker[Session], *, job_types: tuple[str, ...], interactive_first: bool,
    fetcher_factory, max_iterations: int | None = None, idle_sleep: float | None = None,
) -> int:
    """Persistent lane worker: drain the lane, sleep briefly when idle, never exit.

    The persistence is the point (codex): a one-shot drain thread that found its queue empty
    would exit and never pick up the predict follow-ups an IO refresh enqueues later — the lane
    must RE-POLL after an empty pass. ``max_iterations``/``idle_sleep`` exist for tests only
    (None = daemon behavior). Returns total jobs processed (meaningful under max_iterations).

    Per-iteration fetcher (same lifetime as the old per-pass drain_concurrent). A transient DB
    error (e.g. the connection dropped across a laptop sleep) backs off and re-loops —
    pool_pre_ping reconnects on the next iteration once the DB is reachable again."""
    total = 0
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            with factory() as session:
                processed = drain(
                    session,
                    fetcher=fetcher_factory() if fetcher_factory is not None else None,
                    job_types=job_types, interactive_first=interactive_first,
                )
        except Exception:  # noqa: BLE001 — one bad iteration must not kill the lane
            logging.exception("lane %s iteration failed; backing off %ss",
                              job_types, ERROR_BACKOFF_SECONDS)
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue
        total += processed
        if processed == 0 and (max_iterations is None or iterations < max_iterations):
            time.sleep(POLL_SECONDS if idle_sleep is None else idle_sleep)
    return total


def main() -> None:  # pragma: no cover — daemon entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = create_ops_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        recover_stale(session)
    if CONFIG.worker_concurrency < 1 or CONFIG.cpu_concurrency < 1:
        raise SystemExit(
            f"OPS_WORKER_CONCURRENCY/OPS_CPU_CONCURRENCY must be >= 1 "
            f"(got io={CONFIG.worker_concurrency} cpu={CONFIG.cpu_concurrency})"
        )
    logging.info(
        "ops worker started (io=%s cpu=%s)",
        CONFIG.worker_concurrency, CONFIG.cpu_concurrency,
    )
    # Persistent per-lane threads (no shared pass barrier): an IO thread grinding through a day's
    # refresh fanout never delays the CPU lane re-polling for a fresh interactive click, and CPU
    # threads pick up predict follow-ups the moment a refresh enqueues them.
    threads = [
        Thread(
            target=_lane_daemon, daemon=True,
            kwargs={"factory": factory, "job_types": _IO_LANE, "interactive_first": False,
                    "fetcher_factory": make_fetcher},
        )
        for _ in range(CONFIG.worker_concurrency)
    ] + [
        Thread(
            target=_lane_daemon, daemon=True,
            kwargs={"factory": factory, "job_types": _CPU_LANE, "interactive_first": True,
                    "fetcher_factory": None},
        )
        for _ in range(CONFIG.cpu_concurrency)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":  # pragma: no cover
    main()
