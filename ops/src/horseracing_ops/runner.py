"""Execute one refresh_race job (Feature 024).

At RUN time (not enqueue time) we re-decide the fetch kind from the live result-pending state:
- no race_results  -> entries + win odds (pre-race overwrite is allowed only while pending)
- has race_results -> results (INSERT-only; never clobbers JRA-VAN finals)

The underlying scrape_* helpers already record their own ingestion_jobs rows + commit; here we roll
their JobSummary counts up onto the refresh_race orchestration row and set its terminal status.
Ingested odds/results never become model features (II) — display data only.
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob, RaceResult
from horseracing_scrape.fetch import HttpFetcher
from horseracing_scrape.pipeline import scrape_entries, scrape_odds, scrape_results
from horseracing_scrape.urls import entries_url, result_url, win_odds_url
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_USER_AGENT = "horseracing-ops/0.1 (personal use; contact via repo)"


def make_fetcher(min_interval: float = 1.0, cache_dir: str | None = None) -> HttpFetcher:
    """A polite fetcher reusing scrape's robots/rate-limit/backoff/cache (FR-014)."""
    return HttpFetcher(user_agent=_USER_AGENT, min_interval_s=min_interval, cache_dir=cache_dir)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def is_result_pending(session: Session, race_id: str) -> bool:
    n = session.scalar(
        select(func.count()).select_from(RaceResult).where(RaceResult.race_id == race_id)
    )
    return not (n and n > 0)


def _terminal(statuses: list[str], written: int, errors: int) -> str:
    if any(s == JobStatus.FAILED for s in statuses):
        return JobStatus.FAILED
    if errors:
        return JobStatus.PARTIAL
    if written == 0:
        # nothing ingested (page not published yet / no rows) — distinct from a real success
        return JobStatus.SKIPPED
    return JobStatus.SUCCEEDED


def run_one(session: Session, job: IngestionJob, *, fetcher=None) -> IngestionJob:
    """Run the job's scrape, set its terminal status/counts/summary, and commit."""
    fetcher = fetcher or make_fetcher()
    race_id = job.scope_value or ""
    pending = is_result_pending(session, race_id)
    kind = "entries+odds" if pending else "results"

    summaries = []
    if pending:
        summaries.append(scrape_entries(session, urls=[entries_url(race_id)], fetcher=fetcher,
                                        scope_value=race_id))
        summaries.append(scrape_odds(session, urls=[win_odds_url(race_id)], fetcher=fetcher,
                                     scope_value=race_id))
    else:
        summaries.append(scrape_results(session, urls=[result_url(race_id)], fetcher=fetcher,
                                        scope_value=race_id))

    processed = sum(s.processed for s in summaries)
    written = sum(s.written for s in summaries)
    skipped = sum(s.skipped for s in summaries)
    errors = sum(s.errors for s in summaries)
    statuses = [s.status for s in summaries]

    job.status = _terminal(statuses, written, errors)
    job.processed_rows = processed
    job.skipped_rows = skipped
    job.error_count = errors
    job.completed_at = _now()
    job.summary = {
        "kind": kind,
        "written": written,
        "calls": [{"job_type": s.job_type, "status": s.status, "written": s.written,
                   "skipped": s.skipped, "errors": s.errors} for s in summaries],
    }
    session.add(job)
    session.commit()
    return job
