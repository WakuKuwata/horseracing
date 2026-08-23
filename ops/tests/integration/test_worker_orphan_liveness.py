"""A claimed job must never be strandable, and a live job must never be reclaimed.

The incident this exists for: on 2026-08-22 two ``refresh_race`` rows sat RUNNING for 13 hours and
were exactly the two races of that day with no results. Startup-only, age-based ``recover_stale``
could not reach them — a row claimed shortly before the worker was killed is YOUNGER than the stale
window at the next startup, and nothing ever looks again. ``enqueue_race`` then reuses any ACTIVE
row, so the race was permanently invisible to the day fan-out.

The other half of the requirement is the one that makes a naive fix dangerous: recovery must not
touch a job that is still running. Measured here, a legitimate refresh_race has run for 1246s
against a 900s window, so re-queueing on age alone would scrape the same race twice.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob
from sqlalchemy.orm import sessionmaker

from horseracing_ops import (
    JOB_TYPE_PREDICT,
    JOB_TYPE_RACE,
    JOB_TYPE_RECOMMEND,
    JOB_TYPE_REFRESH_RANGE,
)
from horseracing_ops.worker import (
    _DETACHED_CHILD_GRACE_S,
    WorkerSingleton,
    claim_one,
    inflight_ids,
    recover_orphaned,
    release_inflight,
    requeue_inflight,
)
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


def _running(session, *, job_type: str = JOB_TYPE_RACE, minutes_ago: float = 0.5,
             retry_count: int = 0) -> IngestionJob:
    job = IngestionJob(
        source=Source.NETKEIBA, job_type=job_type, scope="race", scope_value=RID,
        status=JobStatus.RUNNING, retry_count=retry_count, max_retry=5,
        started_at=datetime.datetime.now(datetime.UTC)
        - datetime.timedelta(minutes=minutes_ago),
    )
    session.add(job)
    session.commit()
    return job


def _queued(session, *, job_type: str = JOB_TYPE_RACE) -> IngestionJob:
    job = IngestionJob(
        source=Source.NETKEIBA, job_type=job_type, scope="race", scope_value=RID,
        status=JobStatus.QUEUED, summary={"refresh_origin": "manual_ui"},
    )
    session.add(job)
    session.commit()
    return job


# --- the incident ------------------------------------------------------------------------------

def test_young_running_job_owned_by_nobody_is_recovered(session):
    """The exact shape that stranded: RUNNING, well inside the 900s stale window, no live owner."""
    seed_race(session, race_id=RID)
    job = _running(session, minutes_ago=0.5)

    assert recover_orphaned(session, inflight=set()) == 1
    session.refresh(job)
    assert job.status == JobStatus.QUEUED
    assert job.started_at is None and job.retry_count == 1


def test_exhausted_retries_fail_rather_than_loop(session):
    seed_race(session, race_id=RID)
    job = _running(session, minutes_ago=0.5, retry_count=5)
    assert recover_orphaned(session, inflight=set()) == 1
    session.refresh(job)
    assert job.status == JobStatus.FAILED and job.completed_at is not None


# --- the half that keeps the fix from being worse than the bug -----------------------------------

def test_a_job_this_process_is_running_is_never_reclaimed(session):
    """No age condition means the in-flight set is the ONLY thing preventing double execution."""
    seed_race(session, race_id=RID)
    job = _running(session, minutes_ago=45)  # far past any stale window; still ours

    assert recover_orphaned(session, inflight={job.ingestion_job_id}) == 0
    session.refresh(job)
    assert job.status == JobStatus.RUNNING


@pytest.mark.parametrize(
    "job_type", [JOB_TYPE_PREDICT, JOB_TYPE_RECOMMEND, JOB_TYPE_REFRESH_RANGE]
)
def test_detached_child_types_keep_an_age_floor(session, job_type):
    """CPU-lane runners shell out; those children outlive a killed parent and keep writing.

    "No thread of mine owns this row" therefore does not yet mean "nothing is running it", so these
    types must not be reclaimed until the subprocess that bounds them could no longer be alive.
    """
    seed_race(session, race_id=RID)
    grace = _DETACHED_CHILD_GRACE_S[job_type]

    young = _running(session, job_type=job_type, minutes_ago=(grace / 60) * 0.5)
    assert recover_orphaned(session, inflight=set()) == 0
    session.refresh(young)
    assert young.status == JobStatus.RUNNING

    young.started_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=grace + 60)
    session.add(young)
    session.commit()
    assert recover_orphaned(session, inflight=set()) == 1
    session.refresh(young)
    assert young.status == JobStatus.QUEUED


def test_io_types_have_no_age_floor(session):
    """refresh_race/refresh_day spawn nothing, so their work really does die with the process —
    and refresh_race is the type that actually stranded, so waiting out a grace would keep the
    incident reproducible."""
    assert JOB_TYPE_RACE not in _DETACHED_CHILD_GRACE_S


def test_grace_covers_the_runners_subprocess_timeouts():
    """A constant drifting under its subprocess timeout would silently re-enable double execution."""
    from horseracing_ops import runner

    assert _DETACHED_CHILD_GRACE_S[JOB_TYPE_PREDICT] >= runner._SERVING_TIMEOUT_S
    assert _DETACHED_CHILD_GRACE_S[JOB_TYPE_RECOMMEND] >= runner._BETTING_TIMEOUT_S
    assert _DETACHED_CHILD_GRACE_S[JOB_TYPE_REFRESH_RANGE] >= runner._LIVE_TIMEOUT_S


# --- ordering: a row must never be visible as RUNNING before it is registered ---------------------

def test_claim_registers_before_the_running_commit_is_visible(session, engine):
    """The one race that could make this design reclaim a job it just claimed.

    `claim_one` holds the row lock until it commits, and recovery selects FOR UPDATE SKIP LOCKED,
    so a concurrent recovery skips the row while the claim is mid-flight. By the time the row is
    visible as RUNNING the registration is already in place.
    """
    seed_race(session, race_id=RID)
    job = _queued(session)
    other = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        claimed = claim_one(session, job_types=(JOB_TYPE_RACE,))
        assert claimed is not None
        assert claimed.ingestion_job_id in inflight_ids()

        # a recovery pass running right now, from a different connection, must not touch it
        assert recover_orphaned(other, inflight=inflight_ids()) == 0
        seen = other.get(IngestionJob, job.ingestion_job_id)
        assert seen is not None and seen.status == JobStatus.RUNNING
    finally:
        release_inflight(job.ingestion_job_id)
        other.close()


def test_a_claim_that_fails_to_commit_does_not_leak_a_registration(session, engine):
    seed_race(session, race_id=RID)
    _queued(session)
    before = inflight_ids()

    broken = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        original = broken.commit

        def boom():
            raise RuntimeError("connection died during claim")

        broken.commit = boom
        with pytest.raises(RuntimeError):
            claim_one(broken, job_types=(JOB_TYPE_RACE,))
        broken.commit = original
        assert inflight_ids() == before
    finally:
        broken.rollback()
        broken.close()


# --- shutdown ------------------------------------------------------------------------------------

def test_shutdown_hands_in_flight_jobs_back_to_the_queue(session):
    """SIGTERM is how the operator restarts the worker, and it is how the incident started."""
    seed_race(session, race_id=RID)
    job = _running(session, minutes_ago=0.1)
    from horseracing_ops.worker import register_inflight

    register_inflight(job.ingestion_job_id)
    try:
        assert requeue_inflight(session) == 1
        session.refresh(job)
        assert job.status == JobStatus.QUEUED and job.started_at is None
    finally:
        release_inflight(job.ingestion_job_id)


def test_requeue_ignores_jobs_that_already_reached_a_terminal_state(session):
    seed_race(session, race_id=RID)
    job = _running(session, minutes_ago=0.1)
    job.status = JobStatus.SUCCEEDED
    session.add(job)
    session.commit()
    from horseracing_ops.worker import register_inflight

    register_inflight(job.ingestion_job_id)
    try:
        assert requeue_inflight(session) == 0
        session.refresh(job)
        assert job.status == JobStatus.SUCCEEDED
    finally:
        release_inflight(job.ingestion_job_id)


# --- the precondition the whole design rests on ---------------------------------------------------

def test_singleton_excludes_a_second_worker(engine):
    """"Nobody owns this row" is answered from THIS process's memory, which is only complete while
    no other worker holds jobs. If the singleton cannot be taken, liveness-based recovery must not
    run at all."""
    first, second = WorkerSingleton(engine), WorkerSingleton(engine)
    try:
        assert first.acquire() is True
        assert first.held() is True
        assert second.acquire() is False
    finally:
        first.release()
    try:
        assert second.acquire() is True
    finally:
        second.release()


def test_singleton_reports_not_held_once_its_connection_is_gone(engine):
    """A lost connection means we can no longer claim to be the sole worker — recovery stands down
    rather than guessing (the failure mode that makes DB-side liveness tokens unsafe here)."""
    s = WorkerSingleton(engine)
    assert s.acquire() is True
    s._conn.invalidate()
    s._conn.close()
    assert s.held() is False
    s.release()


def test_unknown_job_ids_in_the_inflight_set_are_harmless(session):
    seed_race(session, race_id=RID)
    job = _running(session, minutes_ago=0.5)
    assert recover_orphaned(session, inflight={uuid.uuid4()}) == 1
    session.refresh(job)
    assert job.status == JobStatus.QUEUED
