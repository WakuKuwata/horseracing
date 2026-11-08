"""One race's 単複 odds from the official site, as ONE audited job (ops 更新ボタン結線・2026-11-07).

176 wired the official route to the CLI only (FR-022). This module is the one piece the ops refresh
job needs on top of it: "this race, now" instead of "this date, all races". It composes the existing
parts without changing any of them — ``Walker.odds_pages`` (index O → day page → the race's 単複
page, the venue and race number read from the cname), ``parse_win_odds`` and ``upsert_jra_odds``
(the shared ``update_odds`` contract: all horses together, precedence by ``official_at``, provenance
``jra_official``, no row creation = netkeiba's entries first, FR-011a).

Why the ops job wants it: netkeiba's odds API answers a burst of requests with HTTP 200 and an
EMPTY ``data.odds`` (measured 2026-10-31 and 2026-11-07: the first ~10 calls of a day refresh
succeed, then nothing for the rest of the day), which the netkeiba parser can only read as "not on
sale yet". The official page has no such behaviour, so the refresh asks it first and keeps netkeiba
as the fallback.

Outcome contract (what the caller branches on):
  * ``written > 0``  — the official odds were adopted; no netkeiba request is needed.
  * ``written == 0`` — anything else: page not reachable (``FAILED`` with the reason), no 単複 page
    for this race on the site (``SKIPPED``), 前日最終 instead of today's odds (``SKIPPED``), or the
    shared writer declined (stale / partial field / awaiting entries — the reasons live in the
    job's error_message). The caller then falls back to netkeiba.

Nothing here retries, caches cnames (FR-001) or waits out a cooldown (FR-005): one walk, one
page, one write. The 単複 page is never archived (constitution V — ``archivable`` denies it)."""

from __future__ import annotations

import datetime

from horseracing_db.enums import Source
from sqlalchemy.orm import Session

from ..models import ParseError
from ..pipeline import JobSummary, _run_job
from ..upsert import Counts
from .navigate import Walker
from .parse.odds import parse_win_odds
from .upsert_jra import upsert_jra_odds

#: ingestion_jobs.job_type for this step — the same name the date-scoped CLI job uses, so one
#: query over job_type covers both; ``scope`` tells them apart ("race" here, "date" in the CLI).
JOB_TYPE = "jra_odds"


def race_want(race_id: str) -> tuple[str, set[tuple[str, int]]]:
    """``(venue_code, {(venue_code, race_no)})`` for ``Walker.odds_pages`` — the same derivation
    the CLI's ``--race-id`` uses (jra/cli.py), kept in one place."""
    venue = race_id[4:6]
    return venue, {(venue, int(race_id[10:12]))}


def refresh_race_win_odds(
    session: Session,
    race_id: str,
    *,
    fetcher,
    race_date: datetime.date,
) -> JobSummary:
    """Fetch and write this race's official 単複 odds as one ``jra_odds`` job (scope=race).

    ``fetcher`` is a ``JraFetcher`` (or a test double with ``.post``/``.get``/``requests_sent``).
    A walk that cannot complete (refusal, cooldown, parameter error, 5xx) raises inside the job and
    is recorded as FAILED by ``_run_job`` — the caller sees ``written == 0`` and falls back."""
    venue, want = race_want(race_id)
    sent_before = getattr(fetcher, "requests_sent", 0)

    def work(job_id) -> Counts:
        walker = Walker(fetcher, date=race_date)
        agg = Counts()
        pages = 0
        for _day, label, resp in walker.odds_pages(venue=venue, want=want):
            pages += 1
            try:
                odds = parse_win_odds(resp.text, expected_date=race_date)
            except ParseError as exc:
                agg.errors += 1
                agg.error_messages.append(f"{race_id}: 単複 page {label}: {exc}")
                continue
            if odds.race_id != race_id:
                # the cname said this race; the page header disagrees — never write it elsewhere
                agg.errors += 1
                agg.error_messages.append(
                    f"{race_id}: 単複 page {label} is for {odds.race_id}; not written"
                )
                continue
            agg.merge(
                upsert_jra_odds(session, odds, observed_at=resp.fetched_at, job_id=job_id)
            )
        if pages == 0:
            agg.skipped += 1
            why = "unsupported_date" if walker.report.unsupported_date else "no 単複 page listed"
            agg.error_messages.append(f"{race_id}: not on the official site ({why})")
        # informational, after the merge (merge SUMS extra keys — these are absolute readings)
        agg.extra["requests_sent"] = getattr(fetcher, "requests_sent", 0) - sent_before
        agg.extra["rewalks"] = walker.report.rewalks
        if walker.report.unsupported_date:
            agg.extra["unsupported_date"] = 1
        return agg

    return _run_job(
        session,
        job_type=JOB_TYPE,
        scope="race",
        scope_value=race_id,
        source=Source.JRA_OFFICIAL,
        work=work,
        pass_job_id=True,
    )
