"""Scrape pipelines with ingestion_jobs audit (R6). fetch -> parse -> upsert, idempotent.

Each run records an ingestion_jobs row (source='netkeiba'). A fatal exception is always recorded
as status=FAILED with completed_at + error_message (codex: never leave a job stuck 'running').
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from horseracing_db.enums import JobStatus, Source
from horseracing_db.models import Horse, IngestionJob, RaceHorse
from horseracing_db.validation import is_valid_race_id
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import SCRAPE_PARSER_VERSION, SURROGATE_PREFIX
from .fetch import PoliteFetcher
from .models import ScrapedRaceList
from .odds_adapter import fetch_win_odds
from .parse._profile import parse_horse_pedigree, parse_horse_profile
from .parse.entries import parse_entries
from .parse.exotic_odds import parse_exotic_odds
from .parse.laps import parse_laps
from .parse.race_list import parse_race_list
from .parse.results import parse_results
from .upsert import (
    Counts,
    backfill_results,
    complete_horse_profile,
    update_odds,
    upsert_entries,
    upsert_exotic_odds,
    upsert_laps,
)
from .urls import horse_pedigree_url, horse_profile_url, race_db_url, race_list_url
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
    session: Session, *, urls: list[str], fetcher: PoliteFetcher, scope_value: str | None = None,
    complete_profiles_after: bool = True,
) -> JobSummary:
    """Ingest entries, then (default) auto-complete leak-safe identity/pedigree for the surrogate
    horses just created — debut/未登録 horses get their sex/birth/血統 without a separate command.

    The completion runs AFTER the entries job commits, scoped to each race's surrogate horses that
    still lack attributes, as its own audited job. It is isolated: a profile-fetch failure records
    a horse_profile job error but never rolls back or fails the entries ingestion (codex: keep the
    entry write deterministic). Set complete_profiles_after=False for entries-only ingestion."""
    scraped_race_ids: list[str] = []

    def work() -> Counts:
        parts: list[Counts] = []
        for u in urls:
            entry = parse_entries(fetcher.get(u))
            rid = _race_id_of(entry.race.key)
            if rid is not None:
                scraped_race_ids.append(rid)
            parts.append(upsert_entries(session, entry))
        return _aggregate(parts)

    summary = _run_job(session, job_type="entries", scope="urls", scope_value=scope_value,
                       work=work)
    if complete_profiles_after and summary.status != JobStatus.FAILED:
        for rid in scraped_race_ids:  # only surrogate horses still missing attrs are fetched
            complete_profiles(session, fetcher=fetcher, race_id=rid)
    return summary


def _race_id_of(key) -> str | None:
    return build_race_id(year=key.year, track_code=key.track_code, kai=key.kai,
                         nichime=key.nichime, race_no=key.race_no)


def scrape_odds(
    session: Session, *, urls: list[str], fetcher: PoliteFetcher, scope_value: str | None = None
) -> JobSummary:
    def work() -> Counts:
        parts: list[Counts] = []
        for u in urls:
            m = re.search(r"race_id=(\d{12})", u)
            if not m:
                parts.append(Counts(skipped=1, error_messages=["no race_id in url"]))
                continue
            race_id = m.group(1)
            # win-odds JSON, fetched no-cache (single-latest, constitution V) via the adapter
            scraped = fetch_win_odds(fetcher, race_id)
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


def scrape_laps(
    session: Session, *, race_ids: list[str], fetcher: PoliteFetcher,
    scope_value: str | None = None,
) -> JobSummary:
    """Ingest race-level sectional lap profiles (034) from db.netkeiba race pages. RESULT-derived,
    single-latest overwrite, audited as job_type='race_laps'. Races with no ラップタイム table or no
    existing race row are skipped (no fake rows)."""
    def work() -> Counts:
        parts: list[Counts] = []
        for rid in race_ids:
            scraped = parse_laps(fetcher.get(race_db_url(rid)), race_id=rid)
            if scraped is None:  # page has no lap section
                parts.append(Counts(processed=1, skipped=1))
                continue
            parts.append(upsert_laps(session, rid, scraped))
        return _aggregate(parts)

    return _run_job(session, job_type="race_laps", scope="race_ids", scope_value=scope_value,
                    work=work)


def discover_races(fetcher: PoliteFetcher, date: str) -> ScrapedRaceList:
    """List a day's race_ids from the server-rendered race-list fragment (③ day discovery).

    Read-only (no DB write, no core-table mutation) — the operator feeds the returned race_ids to
    ``scrape-entries``/``scrape-results`` etc. Only valid 12-digit race_ids are returned; non-JRA
    venues are not filtered here (the entries upsert skips unknown venues, no fake IDs)."""
    scraped = parse_race_list(fetcher.get(race_list_url(date), use_cache=False), date)
    return ScrapedRaceList(
        kaisai_date=scraped.kaisai_date,
        race_ids=tuple(r for r in scraped.race_ids if is_valid_race_id(r)),
    )


def complete_profiles(
    session: Session, *, fetcher: PoliteFetcher,
    netkeiba_horse_ids: list[str] | None = None, race_id: str | None = None,
    limit: int | None = None,
) -> JobSummary:
    """Fill leak-safe identity/pedigree (④) for surrogate horses.

    Targets ``nk:`` surrogate horses still missing identity/pedigree (sex/birth_year/sire) — an
    explicit netkeiba-id list, optionally scoped to one ``race_id`` — fetches each db.netkeiba.com
    profile, and fills NULL columns only (never clobbers JRA-VAN, never reads career stats). Invoked
    via the ``complete-profiles`` CLI or automatically after ``scrape_entries`` (race-scoped)."""
    def work() -> Counts:
        if netkeiba_horse_ids is not None:
            targets = [(f"{SURROGATE_PREFIX}{nk}", nk) for nk in netkeiba_horse_ids]
        else:
            stmt = (
                select(Horse.horse_id)
                .where(Horse.horse_id.like(f"{SURROGATE_PREFIX}%"))
                .where(or_(Horse.sex.is_(None), Horse.birth_year.is_(None),
                           Horse.sire_id.is_(None)))
                .order_by(Horse.horse_id)
            )
            if race_id is not None:  # scope to one race's surrogate horses (auto-after-entries)
                stmt = stmt.where(
                    Horse.horse_id.in_(
                        select(RaceHorse.horse_id).where(RaceHorse.race_id == race_id)
                    )
                )
            if limit is not None:
                stmt = stmt.limit(limit)
            targets = [(hid, hid[len(SURROGATE_PREFIX):]) for hid in session.scalars(stmt)]

        parts: list[Counts] = []
        for horse_id, netkeiba_id in targets:
            try:
                profile = parse_horse_profile(
                    fetcher.get(horse_profile_url(netkeiba_id)), netkeiba_id
                )
            except Exception as exc:  # noqa: BLE001 — one bad page must not abort the pass
                parts.append(Counts(processed=1, errors=1, error_messages=[str(exc)]))
                continue
            # pedigree lives on a separate server-rendered page; its failure must not drop the
            # identity we already have — merge what we can (Unknown pedigree stays None).
            try:
                sire, dam, damsire = parse_horse_pedigree(
                    fetcher.get(horse_pedigree_url(netkeiba_id)), netkeiba_id
                )
                profile = replace(
                    profile,
                    netkeiba_sire_id=sire[0], sire_name=sire[1],
                    netkeiba_dam_id=dam[0], dam_name=dam[1],
                    netkeiba_damsire_id=damsire[0], damsire_name=damsire[1],
                )
            except Exception as exc:  # noqa: BLE001 — pedigree optional; keep identity
                parts.append(Counts(error_messages=[f"pedigree skipped {netkeiba_id}: {exc}"]))
            parts.append(complete_horse_profile(session, horse_id, profile))
        return _aggregate(parts)

    return _run_job(session, job_type="horse_profile", scope="surrogate_horses",
                    scope_value=str(limit) if limit is not None else None, work=work)


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
