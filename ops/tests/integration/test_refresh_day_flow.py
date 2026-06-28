"""T022 (US2, A): a day batch DISCOVERS races from netkeiba (worker) and fans out per-race
children; partial failure + failed-only re-run; empty day is a clean no-op."""

from __future__ import annotations

import datetime

import pytest

from horseracing_ops.enqueue import count_by, enqueue_day_parent, enqueue_race
from horseracing_ops.worker import drain
from tests.conftest import RID_NO_FIXTURE

pytestmark = pytest.mark.integration

DATE = datetime.date(2024, 12, 28)


def test_day_batch_discovers_then_partial_then_rerun_failed(session, fixture_fetcher):
    # POST-time: only the parent refresh_day is created (no DB races needed, no children yet).
    parent = enqueue_day_parent(session, DATE)
    session.commit()
    trace_id = parent.trace_id

    # worker drains: refresh_day -> discover (race-list fixture) -> fan out 2 children -> run each.
    drain(session, fetcher=fixture_fetcher)

    counts = count_by(session, trace_id)
    assert counts.get("succeeded") == 1   # REAL_RID (has fixtures)
    assert counts.get("failed") == 1      # RID_NO_FIXTURE (entries fetch fails)

    # failed-only re-run: enqueue just the failed race -> a brand-new queued child job
    job, reused = enqueue_race(session, RID_NO_FIXTURE)
    session.commit()
    assert reused is False and job.status == "queued"


def test_day_with_no_races_is_clean_noop(session, fixture_fetcher):
    # discovery yields 0 races -> parent SUCCEEDED, 0 children (no orphan, no 404, no error).
    parent = enqueue_day_parent(session, datetime.date(2024, 1, 1))
    session.commit()
    drain(session, fetcher=fixture_fetcher)
    session.refresh(parent)
    assert parent.status == "succeeded"
    assert count_by(session, parent.trace_id) == {}
