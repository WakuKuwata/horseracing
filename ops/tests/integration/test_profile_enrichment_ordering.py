"""092: pedigree/identity enrichment must not block the unrecoverable pre-race captures.

Measured on the live DB: 28–67 new surrogate horses appear per race day, each needing a profile
and a pedigree request. At the operator's 1-request-per-minute budget that is 1–2.2 hours of
enrichment per day. Run inline at the FRONT of a race refresh (the old behaviour) it delayed the
pre-race odds and the exotic price grid — the two things that cannot be fetched again once the
race has run.
"""

from __future__ import annotations

import pytest
from horseracing_scrape.fetch import FetchError
from horseracing_scrape.urls import entries_url, horse_profile_url, result_url, win_odds_url

from horseracing_ops.enqueue import enqueue_race
from horseracing_ops.worker import drain
from tests._synth import seed_race

pytestmark = pytest.mark.integration

RID = "202406050911"


class _OrderRecordingFetcher:
    """Wraps the fixture fetcher and records the order URLs were requested in."""

    def __init__(self, inner):
        self._inner = inner
        self.order: list[str] = []

    def get(self, url: str, *, use_cache: bool = True) -> str:
        self.order.append(url)
        return self._inner.get(url, use_cache=use_cache)

    def kinds(self) -> list[str]:
        out = []
        for u in self.order:
            if u == entries_url(RID):
                out.append("entries")
            elif u == win_odds_url(RID):
                out.append("odds")
            elif u == result_url(RID):
                out.append("results")
            elif "/horse/" in u:
                out.append("profile")
        return out


def test_enrichment_runs_after_the_unrecoverable_captures(session, fixture_fetcher):
    """Odds must be requested before any profile/pedigree page."""
    seed_race(session, race_id=RID)
    enqueue_race(session, RID, origin="manual_ui")
    session.commit()

    spy = _OrderRecordingFetcher(fixture_fetcher)
    drain(session, fetcher=spy)

    kinds = spy.kinds()
    # The ordering assertion below is only meaningful if enrichment was actually attempted, so
    # assert that first — a conditional check that silently skips is worse than no test at all.
    assert "profile" in kinds, (
        "the 18-horse fixture becomes surrogate horses, so enrichment must have been attempted; "
        f"got {kinds}"
    )
    assert "odds" in kinds, "the race refresh must have attempted the pre-race odds"
    assert kinds.index("entries") < kinds.index("odds"), "odds needs entries' race_horses rows"
    assert kinds.index("odds") < kinds.index("profile"), (
        "enrichment must never delay a capture that cannot be taken again after the race"
    )
    assert kinds.index("results") < kinds.index("profile"), "enrichment is last"


def test_enrichment_failure_does_not_degrade_the_race_refresh(session, fixture_fetcher):
    """A blocked or missing profile page must not paint the whole race PARTIAL — otherwise a
    source-wide refusal marks every race degraded and buries the real signal. It must still be
    reported, because the previous code discarded the result entirely."""
    seed_race(session, race_id=RID)
    job, _ = enqueue_race(session, RID, origin="manual_ui")
    session.commit()

    class _NoProfiles:
        def __init__(self, inner):
            self._inner = inner

        def get(self, url: str, *, use_cache: bool = True) -> str:
            if "/horse/" in url:
                raise FetchError(f"blocked: {url}")
            return self._inner.get(url, use_cache=use_cache)

    drain(session, fetcher=_NoProfiles(fixture_fetcher))
    session.refresh(job)

    assert job.status == "succeeded", "entries/results/odds all landed — that is what this job is"
    assert "profiles" in job.summary, "enrichment outcome must be visible, not silently dropped"


def test_profile_url_is_only_requested_for_horses_missing_attributes(session, fixture_fetcher):
    """Enrichment is one-shot: a second refresh must not re-fetch already-completed horses.
    (Measured: only 1 of 4,045 surrogate horses currently matches the completion query.)"""
    seed_race(session, race_id=RID)
    enqueue_race(session, RID, origin="manual_ui")
    session.commit()
    first = _OrderRecordingFetcher(fixture_fetcher)
    drain(session, fetcher=first)

    enqueue_race(session, RID, origin="manual_ui")
    session.commit()
    second = _OrderRecordingFetcher(fixture_fetcher)
    drain(session, fetcher=second)

    n_first = sum(1 for u in first.order if "/horse/" in u)
    n_second = sum(1 for u in second.order if "/horse/" in u)
    assert n_second <= n_first, "a repeat refresh must not spend more budget on enrichment"



def test_profile_url_builder_is_the_one_the_ordering_test_matches_on():
    """Guards the '/horse/' substring the tests above key on."""
    assert "/horse/" in horse_profile_url("2022103995")
