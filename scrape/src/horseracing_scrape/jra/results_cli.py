"""`jra-results` / `jra-payouts`(Feature 176, contracts/cli.md・tasks T078).

``jra-results``: 索引 S → 開催日 → 全レース結果ページ。同じ解析結果を 2 つの writer に
各 job の ID で 1 回ずつ渡す(job ``jra_results`` と ``jra_payouts``)。
取得件数は ``jra_results`` 側に計上し、
``jra_payouts`` は ``requests_sent=0`` + ``shared_fetch_with_jra_results=1``(codex Q2-6)。

``jra-payouts``: 索引 H → 払戻金ページ(開催日 1 ページ)。job ``jra_payouts`` 単独。
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import Source

from ..models import ParseError
from ..pipeline import _run_job
from ..upsert import Counts
from .navigate import Walker
from .parse.payouts import parse_payouts_day
from .parse.results import parse_results_day

JRA = Source.JRA_OFFICIAL


def _result():
    from .cli import RunResult

    return RunResult


def _skip_jobs(session, result, job_types, date, requests_sent):
    for i, jt in enumerate(job_types):
        result.jobs.append(
            _run_job(
                session,
                job_type=jt,
                scope="date",
                scope_value=date.isoformat(),
                source=JRA,
                work=lambda i=i: Counts(
                    skipped=1,
                    extra={"unsupported_date": 1, "requests_sent": requests_sent if i == 0 else 0},
                ),
            )
        )


def run_results(args, *, fetcher, session_factory, date: datetime.date):
    from .cli import _collect_summaries
    from .upsert_jra import upsert_jra_payouts, upsert_jra_results

    result = _result()("jra-results", date, dry_run=bool(args.dry_run))
    walker = Walker(fetcher, date=date)
    pages = walker.day_pages("results", venue=args.venue)
    result.requests_sent = fetcher.requests_sent
    if not pages:
        result.unsupported_date = 1
        result.notes.append(f"unsupported_date: {date} is not on the results index")
        if not args.dry_run:
            with session_factory() as session:
                _skip_jobs(
                    session, result, ("jra_results", "jra_payouts"), date, fetcher.requests_sent
                )
        return result
    parsed = []
    errors: list[str] = []
    for day, resp in pages:
        try:
            parsed.append((resp, parse_results_day(resp.text, expected_date=date)))
        except ParseError as exc:
            errors.append(f"results page {day.venue_code}: {exc}")
    if args.dry_run:
        for _, page in parsed:
            result.notes.append(f"{page.venue_code}: {page.state_counts()}")
        result.notes.extend(errors)
        return result

    wanted = set(args.race_ids) if args.race_ids else None

    with session_factory() as session:

        def work_results(job_id) -> Counts:
            agg = Counts(
                extra={"requests_sent": fetcher.requests_sent, "rewalks": walker.report.rewalks}
            )
            for resp, page in parsed:
                for k, v in page.state_counts().items():
                    agg.bump(k, v)
                for b in page.races:
                    if b.state == "parse_failed":
                        agg.errors += 1
                        agg.error_messages.append(f"results block {b.race_no}: {b.error}")
                agg.merge(
                    upsert_jra_results(
                        session,
                        page,
                        observed_at=resp.fetched_at,
                        job_id=job_id,
                        race_ids=args.race_ids,
                    )
                )
            for e in errors:
                agg.errors += 1
                agg.error_messages.append(e)
            return agg

        j1 = _run_job(
            session,
            job_type="jra_results",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work_results,
            pass_job_id=True,
        )
        result.jobs.append(j1)

        def work_payouts(job_id) -> Counts:
            agg = Counts(extra={"requests_sent": 0, "shared_fetch_with_jra_results": 1})
            for resp, page in parsed:
                for b in page.published():
                    if wanted is not None and b.race_id not in wanted:
                        continue
                    agg.merge(
                        upsert_jra_payouts(
                            session,
                            b.race_id,
                            b.payload.payouts,
                            observed_at=resp.fetched_at,
                            job_id=job_id,
                            html_sha256=resp.raw_sha256,
                        )
                    )
            return agg

        j2 = _run_job(
            session,
            job_type="jra_payouts",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work_payouts,
            pass_job_id=True,
        )
        result.jobs.append(j2)
        _collect_summaries(session, result, (j1, j2))
    return result


def run_payouts(args, *, fetcher, session_factory, date: datetime.date):
    from .cli import _collect_summaries
    from .upsert_jra import upsert_jra_payouts

    result = _result()("jra-payouts", date, dry_run=bool(args.dry_run))
    walker = Walker(fetcher, date=date)
    pages = walker.day_pages("payouts", venue=args.venue)
    result.requests_sent = fetcher.requests_sent
    if not pages:
        result.unsupported_date = 1
        result.notes.append(f"unsupported_date: {date} is not on the payouts index")
        if not args.dry_run:
            with session_factory() as session:
                _skip_jobs(session, result, ("jra_payouts",), date, fetcher.requests_sent)
        return result
    parsed = []
    errors: list[str] = []
    for day, resp in pages:
        try:
            parsed.append((resp, parse_payouts_day(resp.text, expected_date=date)))
        except ParseError as exc:
            errors.append(f"payouts page {day.venue_code}: {exc}")
    if args.dry_run:
        for _, page in parsed:
            result.notes.append(f"{page.venue_code}: {page.state_counts()}")
        result.notes.extend(errors)
        return result
    wanted = set(args.race_ids) if args.race_ids else None

    with session_factory() as session:

        def work(job_id) -> Counts:
            agg = Counts(
                extra={"requests_sent": fetcher.requests_sent, "rewalks": walker.report.rewalks}
            )
            for resp, page in parsed:
                for k, v in page.state_counts().items():
                    agg.bump(k, v)
                for b in page.races:
                    if b.state == "parse_failed":
                        agg.errors += 1
                        agg.error_messages.append(f"payouts block {b.race_no}: {b.error}")
                for b in page.published():
                    if wanted is not None and b.race_id not in wanted:
                        continue
                    agg.merge(
                        upsert_jra_payouts(
                            session,
                            b.race_id,
                            b.payload,
                            observed_at=resp.fetched_at,
                            job_id=job_id,
                            html_sha256=resp.raw_sha256,
                        )
                    )
            for e in errors:
                agg.errors += 1
                agg.error_messages.append(e)
            return agg

        j = _run_job(
            session,
            job_type="jra_payouts",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work,
            pass_job_id=True,
        )
        result.jobs.append(j)
        _collect_summaries(session, result, (j,))
    return result
