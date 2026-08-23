"""A settled race must not spend a netkeiba request re-asking for odds it can no longer be given.

``run_one`` fires entries → results → odds on every refresh regardless of race state. Once a race
is settled netkeiba stops serving win odds, so that third request failed by construction and
dragged the whole race job to PARTIAL — which is also why 572 refresh_race rows on this DB are
PARTIAL and why a genuinely broken sub-step had nowhere to stand out.

This is the orchestration-level statement of the rule proven in
``scrape/tests/integration/test_odds_already_final.py``: refreshing the same race twice must issue
the win-odds request exactly ONCE, and the second pass must come back SUCCEEDED rather than PARTIAL.
"""

from __future__ import annotations

import pytest
from horseracing_db.enums import JobStatus
from horseracing_scrape.urls import win_odds_url

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests.conftest import REAL_RID

pytestmark = pytest.mark.integration


class CountingFetcher:
    """Wraps the fixture fetcher and records every URL asked for."""

    def __init__(self, inner):
        self._inner = inner
        self.urls: list[str] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.urls.append(url)
        return self._inner.get(url, use_cache=use_cache)


def _odds_requests(fetcher: CountingFetcher) -> int:
    return sum(1 for u in fetcher.urls if u == win_odds_url(REAL_RID))


def test_second_refresh_of_a_settled_race_asks_for_odds_once(session, fixture_fetcher):
    counting = CountingFetcher(fixture_fetcher)

    job, _ = enqueue_race(session, REAL_RID, origin="manual_ui")
    session.commit()
    drain(session, fetcher=counting)
    session.refresh(job)
    assert job.status == JobStatus.SUCCEEDED
    assert _odds_requests(counting) == 1  # first pass: the race had no odds yet

    # force past the freshness window so this really is a second full pass over the same race
    again, reused = enqueue_race(session, REAL_RID, origin="manual_ui", force=True)
    session.commit()
    assert reused is False
    drain(session, fetcher=counting)
    session.refresh(again)

    assert _odds_requests(counting) == 1, "the settled race must not be re-asked for its odds"
    assert again.status == JobStatus.SUCCEEDED, (
        "a settled race is not a partial failure — the odds step has nothing left to do"
    )
    odds_calls = [c for c in (again.summary or {}).get("calls", []) if c["job_type"] == "odds"]
    assert [c["status"] for c in odds_calls] == [JobStatus.SKIPPED]
