"""Day refresh: the request-time ``force`` flag must survive to the worker's fan-out, and the batch
aggregate must account for EVERY child (and for the races it never touched).

Both were silent holes. ``POST /days/{date}/refresh`` accepted ``force`` from the published
contract and dropped it on the floor, so a re-press inside the 600s freshness window issued no
netkeiba request at all. And the aggregate only counted succeeded/failed/running, so PARTIAL
children (the normal state of a race that has not run yet) and reused children (absent from the
batch entirely, because they keep their original trace_id) were invisible.
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob
from sqlalchemy import select

from horseracing_ops import JOB_TYPE_RACE
from horseracing_ops.enqueue import enqueue_day_parent
from horseracing_ops.worker import drain
from tests.conftest import REAL_RID, RID_NO_FIXTURE

pytestmark = pytest.mark.integration

DATE = datetime.date(2024, 12, 28)


def _race_jobs(session, race_id: str) -> list[IngestionJob]:
    return list(
        session.scalars(
            select(IngestionJob)
            .where(IngestionJob.job_type == JOB_TYPE_RACE)
            .where(IngestionJob.scope_value == race_id)
            .order_by(IngestionJob.created_at.asc())
        )
    )


def test_day_without_force_reuses_a_fresh_child(session, fixture_fetcher):
    """Baseline: the freshness window is real — a second day refresh re-fetches nothing."""
    first = enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)
    assert [j.status for j in _race_jobs(session, REAL_RID)] == [JobStatus.SUCCEEDED]

    second = enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    # still exactly one job for that race: the fan-out reused the fresh one.
    # (children_new is 1, not 0: the day's OTHER discovered race failed, and a failed job is
    # correctly re-run — only a fresh SUCCESS is reused.)
    assert len(_race_jobs(session, REAL_RID)) == 1
    assert second.summary["children_new"] == 1
    assert second.summary["force"] is False
    assert first.trace_id != second.trace_id


def test_day_with_force_enqueues_a_fresh_child(session, fixture_fetcher):
    """force=true must reach ``enqueue_race`` in the WORKER, not just the request handler."""
    enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)
    assert len(_race_jobs(session, REAL_RID)) == 1

    forced = enqueue_day_parent(session, DATE, force=True)
    session.commit()
    assert forced.summary["force"] is True  # carried on the parent until the worker claims it
    drain(session, fetcher=fixture_fetcher)

    jobs = _race_jobs(session, REAL_RID)
    assert len(jobs) == 2, "force must bypass the freshness window and re-fetch"
    assert jobs[-1].trace_id == forced.trace_id
    assert forced.summary["children_new"] == 2


def test_force_survives_the_http_layer(client):
    """The endpoint reads body.force — previously it parsed the body and ignored the field."""
    trace_id = client.post(
        f"/ops/v1/days/{DATE.isoformat()}/refresh", json={"force": True}
    ).json()["trace_id"]
    r = client.get(f"/ops/v1/batches/{trace_id}")
    assert r.status_code == 200


def test_batch_counts_partial_and_reports_discovered(client, session, fixture_fetcher):
    """PARTIAL children are counted, and `discovered` exposes races this batch never touched."""
    parent = enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    # force one child to the state a not-yet-run race ends in (results page has no table yet)
    child = _race_jobs(session, REAL_RID)[0]
    child.status = JobStatus.PARTIAL
    session.add(child)
    session.commit()

    body = client.get(f"/ops/v1/batches/{parent.trace_id}").json()
    assert body["partial"] == 1
    assert body["discovered"] == 2  # both discovered races belong to THIS batch
    assert body["succeeded"] + body["partial"] + body["failed"] + body["skipped"] == body["total"]


def test_a_reused_race_still_belongs_to_the_batch(client, session, fixture_fetcher):
    """A reused child keeps its ORIGINAL trace_id, so a trace-scoped batch could not see it.

    That produced 「完了 1/1 成功」 for a day whose other race was silently untouched (and
    「完了 0/0 成功」 when every race was reused). The batch is a question about a DAY, so it is
    answered about the day: every discovered race contributes its current job, whoever enqueued it.
    """
    enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    second = enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    body = client.get(f"/ops/v1/batches/{second.trace_id}").json()
    assert body["discovered"] == 2, "the day HAD 2 races"
    assert body["total"] == 2, "the reused race is still one of the day's races"
    assert {c["scope_value"] for c in body["children"]} == {REAL_RID, RID_NO_FIXTURE}
    # only ONE race was actually re-enqueued by this click — that is what `enqueued` is for
    assert body["enqueued"] == 1

    empty_day = enqueue_day_parent(session, datetime.date(2024, 1, 1))
    session.commit()
    drain(session, fetcher=fixture_fetcher)
    empty = client.get(f"/ops/v1/batches/{empty_day.trace_id}").json()
    assert empty["total"] == 0 and empty["discovered"] == 0


def test_a_reused_races_later_failure_is_still_reported(client, session, fixture_fetcher):
    """The reason this matters: a race the operator asked about failed, and the old batch —
    scoped to the rows THIS click created — reported success anyway."""
    enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    second = enqueue_day_parent(session, DATE)
    session.commit()
    drain(session, fetcher=fixture_fetcher)

    # the reused race (a fresh success from the first pass) goes bad afterwards
    reused = _race_jobs(session, REAL_RID)[-1]
    reused.status = JobStatus.FAILED
    session.add(reused)
    session.commit()

    body = client.get(f"/ops/v1/batches/{second.trace_id}").json()
    # name the race: the day's OTHER race fails on its own (no fixture), so a bare failed>=1 would
    # pass even against a trace-scoped batch that cannot see the reused one at all.
    reported = {c["scope_value"]: c["status"] for c in body["children"]}
    assert reported.get(REAL_RID) == JobStatus.FAILED
    assert body["status"] != JobStatus.SUCCEEDED
