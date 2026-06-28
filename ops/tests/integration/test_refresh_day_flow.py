"""T022 (US2): day batch fans out to per-race children; partial failure + failed-only re-run."""

from __future__ import annotations

import datetime

import pytest

from horseracing_ops.enqueue import count_by, enqueue_day, enqueue_race
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

DATE = datetime.date(2024, 12, 28)
RID_OK = "202406050911"   # has fixtures -> succeeds
RID_BAD = "202406050912"  # no fixture -> entries fetch fails -> failed


def test_day_batch_partial_then_rerun_failed(session, fixture_fetcher):
    seed_race(session, race_id=RID_OK)
    seed_race(session, race_id=RID_BAD)

    parent, children = enqueue_day(session, DATE)
    session.commit()
    assert len(children) == 2

    drain(session, fetcher=fixture_fetcher)

    counts = count_by(session, parent.trace_id)
    assert counts.get("succeeded") == 1   # RID_OK
    assert counts.get("failed") == 1      # RID_BAD (no fixture)

    # failed-only re-run: enqueue just the failed race -> a brand-new queued child job
    job, reused = enqueue_race(session, RID_BAD)
    session.commit()
    assert reused is False and job.status == "queued"
