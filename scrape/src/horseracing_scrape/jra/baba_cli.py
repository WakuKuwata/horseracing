"""`jra-baba`(Feature 176 US4・tasks T093).

``/keiba/baba/_data_cushion.html`` と ``_data_moist.html`` を GET、``accessJ`` を POST(計 3 件)。
0024 未適用(``jra_track_conditions`` が無い)なら 1 件も送らず exit 2(``table_missing``)。
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import Source
from sqlalchemy import text

from ..models import ParseError
from ..pipeline import _run_job
from ..upsert import Counts
from .models import KIND_ACCESS_J, KIND_BABA_FRAGMENT
from .navigate import ACCESS_J_CNAME, ACCESS_J_URL, BABA_CUSHION_URL, BABA_MOIST_URL
from .parse.baba import parse_cushion, parse_kaisai_info, parse_moisture

JRA = Source.JRA_OFFICIAL


def table_present(session) -> bool:
    return session.execute(text("SELECT to_regclass('jra_track_conditions')")).scalar() is not None


def run_baba(args, *, fetcher, session_factory, date: datetime.date):
    from .cli import RunResult, _collect_summaries
    from .upsert_jra import upsert_jra_baba

    result = RunResult("jra-baba", date, dry_run=bool(args.dry_run))
    with session_factory() as session:
        present = table_present(session)
    if not present:
        result.notes.append("table_missing: jra_track_conditions (apply migration 0024 first)")
        result.exit_code = 2
        return result
    pages = [
        (fetcher.get(BABA_CUSHION_URL, kind=KIND_BABA_FRAGMENT), parse_cushion),
        (fetcher.get(BABA_MOIST_URL, kind=KIND_BABA_FRAGMENT), parse_moisture),
        (fetcher.post(ACCESS_J_URL, ACCESS_J_CNAME, kind=KIND_ACCESS_J), parse_kaisai_info),
    ]
    result.requests_sent = fetcher.requests_sent
    parsed = []
    errors: list[str] = []
    for resp, parser in pages:
        try:
            parsed.append((resp, parser(resp.text, fetched_at=resp.fetched_at)))
        except ParseError as exc:
            errors.append(f"baba {resp.url.rsplit('/', 1)[-1]}: {exc}")
    if args.dry_run:
        result.notes.extend(
            f"{resp.url.rsplit('/', 1)[-1]}: {len(rs)} reading(s)" for resp, rs in parsed
        )
        result.notes.extend(errors)
        return result

    with session_factory() as session:

        def work(job_id) -> Counts:
            agg = Counts(extra={"requests_sent": fetcher.requests_sent})
            for resp, readings in parsed:
                agg.merge(
                    upsert_jra_baba(
                        session,
                        readings,
                        fetched_at=resp.fetched_at,
                        raw_sha256=resp.raw_sha256,
                        job_id=job_id,
                    )
                )
            for e in errors:
                agg.errors += 1
                agg.error_messages.append(e)
            return agg

        j = _run_job(
            session,
            job_type="jra_baba",
            scope="date",
            scope_value=date.isoformat(),
            source=JRA,
            work=work,
            pass_job_id=True,
        )
        result.jobs.append(j)
        _collect_summaries(session, result, (j,))
    return result
