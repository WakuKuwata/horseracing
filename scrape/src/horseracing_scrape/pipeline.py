"""Scrape pipelines with ingestion_jobs audit (R6). fetch -> parse -> upsert, idempotent.

Each run records an ingestion_jobs row (source='netkeiba'). A fatal exception is always recorded
as status=FAILED with completed_at + error_message (codex: never leave a job stuck 'running').
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import IngestionJob
from sqlalchemy.orm import Session

from . import SCRAPE_PARSER_VERSION
from .fetch import PoliteFetcher
from .parse.entries import parse_entries
from .parse.exotic_odds import parse_exotic_odds
from .parse.odds import parse_odds
from .parse.results import parse_results
from .upsert import (
    Counts,
    backfill_results,
    update_odds,
    upsert_entries,
    upsert_exotic_odds,
)
from .venues import build_race_id


@dataclass
class JobSummary:
    job_type: str
    scope_value: str | None
    processed: int
    written: int
    skipped: int
    errors: int
    status: str


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _run_job(
    session: Session, *, job_type: str, scope: str, scope_value: str | None,
    work: Callable[[], Counts],
) -> JobSummary:
    job = IngestionJob(
        source=Source.NETKEIBA, job_type=job_type, scope=scope, scope_value=scope_value,
        status=JobStatus.RUNNING, started_at=_now(),
    )
    session.add(job)
    session.flush()
    try:
        c = work()
        status = JobStatus.PARTIAL if c.errors else JobStatus.SUCCEEDED
    except Exception as exc:  # noqa: BLE001 — fatal: record FAILED, never leave 'running'
        session.rollback()
        job = IngestionJob(
            source=Source.NETKEIBA, job_type=job_type, scope=scope, scope_value=scope_value,
            status=JobStatus.FAILED, started_at=_now(), completed_at=_now(),
            error_message=str(exc), summary={"parser_version": SCRAPE_PARSER_VERSION},
        )
        session.add(job)
        session.commit()
        return JobSummary(job_type, scope_value, 0, 0, 0, 1, JobStatus.FAILED)

    job.status = status
    job.processed_rows = c.processed
    job.skipped_rows = c.skipped
    job.error_count = c.errors
    job.completed_at = _now()
    job.error_message = "\n".join(c.error_messages[:50]) or None
    job.summary = {"parser_version": SCRAPE_PARSER_VERSION, "written": c.written}
    session.commit()
    return JobSummary(job_type, scope_value, c.processed, c.written, c.skipped, c.errors, status)


def _aggregate(parts: list[Counts]) -> Counts:
    agg = Counts()
    for c in parts:
        agg.processed += c.processed
        agg.written += c.written
        agg.skipped += c.skipped
        agg.errors += c.errors
        agg.error_messages.extend(c.error_messages)
    return agg


def scrape_entries(
    session: Session, *, urls: list[str], fetcher: PoliteFetcher, scope_value: str | None = None
) -> JobSummary:
    def work() -> Counts:
        return _aggregate([upsert_entries(session, parse_entries(fetcher.get(u))) for u in urls])

    return _run_job(session, job_type="entries", scope="urls", scope_value=scope_value, work=work)


def _race_id_of(key) -> str | None:
    return build_race_id(year=key.year, track_code=key.track_code, kai=key.kai,
                         nichime=key.nichime, race_no=key.race_no)


def scrape_odds(
    session: Session, *, urls: list[str], fetcher: PoliteFetcher, scope_value: str | None = None
) -> JobSummary:
    def work() -> Counts:
        parts: list[Counts] = []
        for u in urls:
            scraped = parse_odds(fetcher.get(u))
            race_id = _race_id_of(scraped.key)
            if race_id is None:
                parts.append(Counts(skipped=1, error_messages=["race_id not constructible"]))
                continue
            parts.append(update_odds(session, race_id, scraped))
        return _aggregate(parts)

    return _run_job(session, job_type="odds", scope="urls", scope_value=scope_value, work=work)


def scrape_exotic_odds(
    session: Session, *, urls: list[str], fetcher: PoliteFetcher, scope_value: str | None = None
) -> JobSummary:
    """Ingest REAL exotic odds (012). race_id via build_race_id (handles <2007 / unknown venue ->
    skip, no fake IDs). Idempotent overwrite; audited as job_type='exotic_odds'."""
    def work() -> Counts:
        parts: list[Counts] = []
        for u in urls:
            scraped = parse_exotic_odds(fetcher.get(u))
            race_id = _race_id_of(scraped.key)
            if race_id is None:  # <2007 or unknown venue — no fake IDs
                parts.append(Counts(skipped=1, error_messages=["race_id not constructible"]))
                continue
            parts.append(upsert_exotic_odds(session, race_id, scraped))
        return _aggregate(parts)

    return _run_job(session, job_type="exotic_odds", scope="urls", scope_value=scope_value,
                    work=work)


def scrape_results(
    session: Session, *, urls: list[str], fetcher: PoliteFetcher, scope_value: str | None = None
) -> JobSummary:
    def work() -> Counts:
        parts: list[Counts] = []
        for u in urls:
            scraped = parse_results(fetcher.get(u))
            race_id = _race_id_of(scraped.key)
            if race_id is None:
                parts.append(Counts(skipped=1, error_messages=["race_id not constructible"]))
                continue
            parts.append(backfill_results(session, race_id, scraped))
        return _aggregate(parts)

    return _run_job(session, job_type="results", scope="urls", scope_value=scope_value, work=work)
