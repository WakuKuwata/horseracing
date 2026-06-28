"""Execute one refresh job (Feature 024).

A ``refresh_race`` job runs a FULL pass — entries + results + odds — in one go (the safety rules in
the scrape layer make this safe at any race state): entries auto-creates the Race/horses + completes
pedigree, results are INSERT-only (never clobber JRA-VAN finals), odds fill-null on finished races /
overwrite while pending. A future (not-yet-run) race simply has no result page yet, so the results
sub-step is best-effort and degrades the job to PARTIAL rather than failing it.

A ``refresh_day`` job DISCOVERS the day's races from netkeiba (``discover_races``) and fans out one
``refresh_race`` child per race (the worker then drains the children). Discovery/fanout runs in the
worker — never in the request handler — so the POST returns 202 without a netkeiba round-trip.

The underlying scrape_* helpers record their own ingestion_jobs rows + commit; here we roll their
counts onto the orchestration row. Ingested odds/results never become model features (II).
"""

from __future__ import annotations

import datetime

import httpx
from horseracing_db.enums import JobStatus
from horseracing_db.models import IngestionJob, RaceResult
from horseracing_scrape.fetch import HttpFetcher
from horseracing_scrape.pipeline import (
    discover_races,
    scrape_entries,
    scrape_odds,
    scrape_results,
)
from horseracing_scrape.urls import entries_url, result_url, win_odds_url
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .enqueue import enqueue_race

_USER_AGENT = "horseracing-ops/0.1 (personal use; contact via repo)"


def make_fetcher(min_interval: float = 1.0, cache_dir: str | None = None) -> HttpFetcher:
    """A polite fetcher reusing scrape's robots/rate-limit/backoff/cache (FR-014).

    Crucially passes a real httpx client — without it the fetcher can only serve cached pages and
    every LIVE netkeiba fetch fails (robots check dereferences a None client)."""
    return HttpFetcher(
        user_agent=_USER_AGENT, min_interval_s=min_interval, cache_dir=cache_dir,
        client=httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0),
    )


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def is_result_pending(session: Session, race_id: str) -> bool:
    n = session.scalar(
        select(func.count()).select_from(RaceResult).where(RaceResult.race_id == race_id)
    )
    return not (n and n > 0)


def _terminal(*, entries_failed: bool, any_failed: bool, errors: int, written: int) -> str:
    if entries_failed:  # couldn't even build the entry population — a real failure
        return JobStatus.FAILED
    if any_failed or errors:  # e.g. a future race has no result page yet -> best-effort PARTIAL
        return JobStatus.PARTIAL
    if written == 0:
        # nothing ingested (page not published yet / no rows) — distinct from a real success
        return JobStatus.SKIPPED
    return JobStatus.SUCCEEDED


def run_one(session: Session, job: IngestionJob, *, fetcher=None) -> IngestionJob:
    """Full refresh of one race: entries + results + odds. Sets terminal status/counts, commits.

    All three run regardless of result-pending state — the scrape-layer safety rules keep it sound
    (results INSERT-only, odds fill-null-on-finished / overwrite-on-pending). A not-yet-run race has
    no result page, so its results sub-step fails benignly and the job ends PARTIAL, not FAILED."""
    fetcher = fetcher or make_fetcher()
    race_id = job.scope_value or ""

    entries = scrape_entries(session, urls=[entries_url(race_id)], fetcher=fetcher,
                             scope_value=race_id)
    results = scrape_results(session, urls=[result_url(race_id)], fetcher=fetcher,
                             scope_value=race_id)
    odds = scrape_odds(session, urls=[win_odds_url(race_id)], fetcher=fetcher, scope_value=race_id)
    summaries = [entries, results, odds]

    written = sum(s.written for s in summaries)
    errors = sum(s.errors for s in summaries)
    job.status = _terminal(
        entries_failed=(entries.status == JobStatus.FAILED),
        any_failed=any(s.status == JobStatus.FAILED for s in summaries),
        errors=errors, written=written,
    )
    job.processed_rows = sum(s.processed for s in summaries)
    job.skipped_rows = sum(s.skipped for s in summaries)
    job.error_count = errors
    job.completed_at = _now()
    job.summary = {
        "kind": "entries+results+odds",
        "written": written,
        "calls": [{"job_type": s.job_type, "status": s.status, "written": s.written,
                   "skipped": s.skipped, "errors": s.errors} for s in summaries],
    }
    session.add(job)
    session.commit()
    return job


def run_day(session: Session, job: IngestionJob, *, fetcher=None) -> IngestionJob:
    """Discover a day's races from netkeiba and fan out one refresh_race child per race.

    Runs in the worker (not the request handler). The children share the parent's trace_id so the
    batch poll (/batches/{trace_id}) aggregates them. 0 races (no JRA racing that day) is a clean
    no-op SUCCEEDED parent. The children are drained in subsequent worker iterations."""
    fetcher = fetcher or make_fetcher()
    date = datetime.date.fromisoformat(job.scope_value or "")
    listing = discover_races(fetcher, date.strftime("%Y%m%d"))

    n_new = 0
    for rid in listing.race_ids:
        _child, reused = enqueue_race(session, rid, trace_id=job.trace_id)
        if not reused:
            n_new += 1

    job.status = JobStatus.SUCCEEDED
    job.processed_rows = len(listing.race_ids)
    job.completed_at = _now()
    job.summary = {"kind": "discover", "races": len(listing.race_ids), "children_new": n_new}
    session.add(job)
    session.commit()
    return job
