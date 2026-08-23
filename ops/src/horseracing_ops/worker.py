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
import os
import signal
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread

from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from sqlalchemy import Engine, and_, case, or_, select, text
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

def _io_rank():
    """IO-lane claim order: the day the operator asked for, then opportunistic patch-up.

    A fan-out writes all its children inside one transaction, so they share `created_at` (server
    `now()`) and FIFO cannot separate them — the UUID tie-break would interleave 36 corner-order
    patch-up races with the race day's own. The day's races carry pre-race odds and an exotic price
    grid that cease to exist once the race runs; a missing passing order can be collected any day.
    So patch-up sorts last, always.
    """
    return case((IngestionJob.summary.op("->>")("refresh_origin") == "corner_backfill", 1),
                else_=0)


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

#: NOTE (092): a refresh_race can legitimately run for tens of minutes once the politeness
#: interval is raised (18 debut horses × profile+pedigree alone is 36 requests). It is tempting to
#: widen the stale window to match — DON'T, not while `recover_stale` is startup-only.
#:
#: The window is not a liveness check; it is a margin against a SECOND worker starting while a
#: first one is alive. Recovery runs once, in `main()`, at which point the previous process is
#: already dead — so any RUNNING row it finds is genuinely orphaned. Widening the window there
#: only makes orphan recovery LATE: a worker that dies 20 minutes into an 84-minute window
#: restarts, judges the orphan "fresh", leaves it RUNNING, and never looks again — the job is
#: stuck forever and dedup then refuses to re-enqueue it. Re-scraping an idempotent race costs
#: requests; a permanently stuck race costs the race.
#:
#: The real fix for long jobs is a heartbeat/lease (recover against LAST PROGRESS, not
#: `started_at`), which needs a schema column and periodic recovery. Until then the generic
#: window stands, and a restart mid-race re-scrapes that race.


# --- liveness ---------------------------------------------------------------------------------
#
# A job row could be left RUNNING forever, which then made its race permanently invisible to the
# day fan-out (`enqueue_race` reuses any ACTIVE row). Two ways in: the operator restarts the worker
# (`scripts/stack.sh` sends SIGTERM) mid-job, or `_run_claimed`'s own error handler fails to commit
# because the connection died. Startup-only, age-based `recover_stale` cannot fix either: a row
# claimed shortly before the kill is younger than the window at the next startup and is never
# looked at again. Measured on this DB: 2026-08-22 left two refresh_race rows RUNNING for 13 hours,
# and they were exactly the two races of that day with no results.
#
# The fix is to stop asking "how old is this row" and start asking "is anyone running it". The
# answer lives in THIS process, deliberately not in the database:
#
#   * A DB-side liveness token (a session advisory lock) is released when its CONNECTION dies, not
#     when the worker dies. This engine sets pool_recycle=300 and the machine is a laptop that
#     sleeps, so a lock connection dying under a live runner is routine here — and it would make
#     recovery reclaim a job that is still executing. Double-scraping is the one outcome this must
#     never produce, so liveness is kept in memory where it cannot be lost independently of the
#     process that owns it.
#   * The in-memory answer is only complete if no OTHER worker holds jobs. That is what the
#     singleton below establishes; when it cannot be established, recovery does nothing and we are
#     back to today's behaviour (a stranded row, never a duplicated one).
#
# The ordering that makes this race-free: `claim_one` registers the job BEFORE committing RUNNING,
# and recovery only ever sees committed RUNNING rows (it takes the same row lock, so an in-flight
# claim transaction is skipped). There is therefore no instant at which a row is RUNNING to
# recovery but unregistered here.

_INFLIGHT: set[uuid.UUID] = set()
_INFLIGHT_LOCK = Lock()

#: set by SIGTERM/SIGINT — lanes stop claiming NEW work and unwind.
_SHUTDOWN = Event()

#: One advisory lock per DATABASE identifying "the worker process". Arbitrary but fixed; the second
#: int is a namespace so this can never collide with `enqueue._lock_race`'s per-race keys.
_SINGLETON_KEY = (0x6F707377, 1)  # "opsw"


def register_inflight(job_id: uuid.UUID) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.add(job_id)


def release_inflight(job_id: uuid.UUID) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.discard(job_id)


def inflight_ids() -> set[uuid.UUID]:
    with _INFLIGHT_LOCK:
        return set(_INFLIGHT)


class WorkerSingleton:
    """Holds "I am the only worker" for the life of the process.

    The lock sits on ONE pinned physical Connection, never on a pooled Session: a Session returns
    its connection to the pool on every commit, which would hand the lock's ownership to whatever
    used that connection next. Because we never unlock, the connection being alive IS the lock
    being held — so `held()` only has to prove the connection still answers. If it stops answering
    we must assume a second worker could have taken over, and recovery stands down.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._conn = None

    def acquire(self) -> bool:
        conn = self._engine.connect()
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(:a, :b)"),
            {"a": _SINGLETON_KEY[0], "b": _SINGLETON_KEY[1]},
        ).scalar()
        if not got:
            conn.close()
            return False
        self._conn = conn
        return True

    def held(self) -> bool:
        if self._conn is None:
            return False
        try:
            return self._conn.execute(text("SELECT 1")).scalar() == 1
        except Exception:  # noqa: BLE001 — a dead connection means we can no longer claim to be sole
            logging.warning("worker singleton connection lost; orphan recovery stands down")
            conn, self._conn = self._conn, None
            # Best-effort: the connection may already be closed, in which case the server has
            # released the lock anyway. Cleanup must not raise out of a liveness probe.
            try:
                conn.invalidate()
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            return False

    def release(self) -> None:
        """Unlock EXPLICITLY, and hard-drop the connection if that cannot be confirmed.

        `Connection.close()` alone returns the connection to the pool with the advisory lock still
        held by that backend, so the next component to draw it inherits ownership and the next
        acquire() fails against a lock nobody thinks they hold. Found by
        `test_singleton_excludes_a_second_worker` re-acquiring after a release.
        """
        if self._conn is None:
            return
        conn, self._conn = self._conn, None
        try:
            conn.execute(
                text("SELECT pg_advisory_unlock(:a, :b)"),
                {"a": _SINGLETON_KEY[0], "b": _SINGLETON_KEY[1]},
            )
        except Exception:  # noqa: BLE001 — never pool a connection that may still hold the lock
            conn.invalidate()
        finally:
            conn.close()


#: How long a job type's DETACHED child could still be writing after its parent worker is gone.
#:
#: CPU-lane runners shell out (`uv run …`) and those children are NOT reaped by the parent dying,
#: so "no thread of mine owns this row" does not yet mean "nothing is running it" (codex). Until a
#: child-lifecycle fix exists, these types keep an age floor at least as large as the subprocess
#: timeout that bounds them. IO-lane jobs (refresh_race / refresh_day) are absent on purpose: they
#: spawn nothing, so their work genuinely dies with the process and they can be reclaimed at once —
#: which matters, because refresh_race is the type that actually stranded.
_DETACHED_CHILD_GRACE_S = {
    JOB_TYPE_PREDICT: 360,          # runner._SERVING_TIMEOUT_S = 300, + margin
    JOB_TYPE_RECOMMEND: 360,        # runner._BETTING_TIMEOUT_S = 300, + margin
    JOB_TYPE_REFRESH_RANGE: 3900,   # runner._LIVE_TIMEOUT_S = 3600, + margin
}


def recover_orphaned(session: Session, *, inflight: set[uuid.UUID] | None = None) -> int:
    """Re-queue RUNNING rows that no live runner owns. Returns the count.

    Caller must have established that this process is the only worker (see `WorkerSingleton`);
    without that, "not in `inflight`" would also match another worker's healthy job.

    Unlike `recover_stale` this has NO age condition for IO jobs — age was only ever a proxy for
    liveness, and a bad one: measured here, a legitimate refresh_race has run for 1246s against a
    900s window, so an age rule applied periodically would re-queue live work and scrape twice.
    """
    inflight = inflight_ids() if inflight is None else inflight
    now = _now()
    rows = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.job_type.in_(_CLAIMABLE))
        .where(IngestionJob.status == JobStatus.RUNNING)
        .with_for_update(skip_locked=True)
    ).all()

    n = 0
    for job in rows:
        if job.ingestion_job_id in inflight:
            continue  # a thread of this process is running it
        grace = _DETACHED_CHILD_GRACE_S.get(job.job_type)
        if grace is not None and job.started_at is not None:
            started = job.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=datetime.UTC)
            if (now - started).total_seconds() < grace:
                continue  # a detached child of a dead parent could still be writing
        _demote(job, reason="orphaned running job (no live runner)")
        n += 1
    session.commit()
    return n


def _demote(job: IngestionJob, *, reason: str) -> None:
    """Send a job back to the queue, or fail it once its retries are spent."""
    if job.retry_count < job.max_retry:
        job.retry_count += 1
        job.status = JobStatus.QUEUED
        job.started_at = None
    else:
        job.status = JobStatus.FAILED
        job.completed_at = _now()
        job.error_message = reason


def requeue_inflight(session: Session) -> int:
    """Shutdown path: hand every job this process still holds back to the queue.

    Called as the LAST thing before the process exits, so the demoted runner gets no chance to keep
    writing after the row has been handed on (there is no fencing token — leaving no window is what
    substitutes for one).
    """
    ids = inflight_ids()
    if not ids:
        return 0
    rows = session.scalars(
        select(IngestionJob)
        .where(IngestionJob.ingestion_job_id.in_(ids))
        .where(IngestionJob.status == JobStatus.RUNNING)
        .with_for_update()
    ).all()
    for job in rows:
        _demote(job, reason="worker shut down while this job was running")
    session.commit()
    return len(rows)


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
        _demote(job, reason="stale running job exceeded max_retry")
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
        else (_io_rank(), IngestionJob.created_at.asc(), IngestionJob.ingestion_job_id.asc())
    )
    job = session.scalars(
        stmt.order_by(*order).with_for_update(skip_locked=True).limit(1)
    ).first()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.started_at = _now()
    # Register BEFORE the commit. Recovery only sees COMMITTED RUNNING rows and takes the same row
    # lock, so it cannot observe this row until the registration is already in place — there is no
    # instant where the row looks RUNNING-but-unowned.
    register_inflight(job.ingestion_job_id)
    try:
        session.commit()  # release the row lock; RUNNING is the claim marker
    except Exception:
        release_inflight(job.ingestion_job_id)
        raise
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
        _demote(job, reason=str(exc))  # error_message is set only when retries are exhausted
        session.add(job)
        session.commit()
    finally:
        # Unconditional: if the handler above ALSO fails (a dead connection makes its commit raise)
        # the row is left RUNNING, and only de-registering here lets recovery see that nobody owns
        # it. That path is one of the two ways rows used to strand.
        release_inflight(job.ingestion_job_id)


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
        if _SHUTDOWN.is_set():
            break
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
    singleton: WorkerSingleton | None = None,
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
        if _SHUTDOWN.is_set():
            break
        iterations += 1
        try:
            with factory() as session:
                processed = drain(
                    session,
                    fetcher=fetcher_factory() if fetcher_factory is not None else None,
                    job_types=job_types, interactive_first=interactive_first,
                )
                if processed == 0 and singleton is not None and singleton.held():
                    # Idle is the safe moment to look for rows nobody owns. Gated on the singleton
                    # because "nobody owns it" is answered from THIS process's memory.
                    n = recover_orphaned(session)
                    if n:
                        logging.warning("re-queued %s orphaned running job(s)", n)
        except Exception:  # noqa: BLE001 — one bad iteration must not kill the lane
            logging.exception("lane %s iteration failed; backing off %ss",
                              job_types, ERROR_BACKOFF_SECONDS)
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue
        total += processed
        if processed == 0 and (max_iterations is None or iterations < max_iterations):
            _SHUTDOWN.wait(POLL_SECONDS if idle_sleep is None else idle_sleep)
    return total


#: how long the shutdown path waits for in-flight jobs to finish on their own before it hands them
#: back to the queue. Short on purpose: a refresh_race can legitimately run for 20 minutes and the
#: operator restarting the worker should not have to wait for it — being re-queued costs a repeat
#: of an idempotent scrape, being stranded costs the race.
SHUTDOWN_GRACE_S = 10.0


def _install_signal_handlers() -> None:  # pragma: no cover — process-level wiring
    """SIGTERM is how `scripts/stack.sh` restarts the worker, and Python's default action for it
    terminates the process without unwinding anything — which is precisely how a claimed row was
    left RUNNING with nobody to clean it up. Handlers run on the main thread even while it is
    parked in Thread.join(), so setting the flag here is enough to reach the shutdown path."""
    def _request_shutdown(signum, _frame):
        logging.info("signal %s received; finishing in-flight work", signum)
        _SHUTDOWN.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _request_shutdown)


def main() -> None:  # pragma: no cover — daemon entrypoint
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    engine = create_ops_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _install_signal_handlers()

    singleton = WorkerSingleton(engine)
    sole_worker = singleton.acquire()
    with factory() as session:
        if sole_worker:
            # Nobody else holds jobs, so every RUNNING row is by definition unowned — no age test
            # needed, which is what lets a row claimed seconds before the last kill be recovered.
            n = recover_orphaned(session, inflight=set())
            if n:
                logging.warning("startup: re-queued %s orphaned running job(s)", n)
        else:
            # Another worker is alive. Its healthy jobs are indistinguishable from orphans here, so
            # fall back to the conservative age rule and leave liveness alone.
            logging.warning("another ops worker holds the singleton; age-based recovery only")
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
    recovery_singleton = singleton if sole_worker else None
    threads = [
        Thread(
            target=_lane_daemon, daemon=True,
            kwargs={"factory": factory, "job_types": _IO_LANE, "interactive_first": False,
                    "fetcher_factory": make_fetcher, "singleton": recovery_singleton},
        )
        for _ in range(CONFIG.worker_concurrency)
    ] + [
        Thread(
            target=_lane_daemon, daemon=True,
            kwargs={"factory": factory, "job_types": _CPU_LANE, "interactive_first": True,
                    "fetcher_factory": None, "singleton": recovery_singleton},
        )
        for _ in range(CONFIG.cpu_concurrency)
    ]
    for t in threads:
        t.start()

    _SHUTDOWN.wait()
    deadline = time.monotonic() + SHUTDOWN_GRACE_S
    for t in threads:
        t.join(timeout=max(0.0, deadline - time.monotonic()))

    # Last thing before the process ends: whatever is still held goes back to the queue. Doing it
    # here, immediately before exiting, is what keeps the demoted runner from writing after the row
    # has been handed on. os._exit skips interpreter teardown for the same reason — a daemon thread
    # must not get another scheduling slot once its job belongs to someone else.
    try:
        with factory() as session:
            n = requeue_inflight(session)
            if n:
                logging.warning("shutdown: re-queued %s in-flight job(s)", n)
    except Exception:  # noqa: BLE001 — never block the exit on the cleanup
        logging.exception("shutdown: could not re-queue in-flight jobs")
    finally:
        singleton.release()
    logging.info("ops worker stopped")
    os._exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
