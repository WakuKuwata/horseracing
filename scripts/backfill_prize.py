"""Backfill `races.prize_money` for the races the JRA-VAN cutover left without it.

Coverage went 2024:100% -> 2025:77.1% -> 2026:0.0% when the feed stopped, and `prize_rel` is the
active model's #1 split feature. A fixed-model kill-test measured the cost of that gap at
**-0.012 to -0.014 winner NLL** (out/prize-killtest/report.json) — the same magnitude as the body
weight skew, and 5-6x the adoption gate's minimum detectable effect.

There is NO code change needed: feature 092 already parses 本賞金 from the entries page and
upserts it fill-if-null, so re-scraping a race is enough. This script just drives that over the
races that need it, politely and resumably.

## Politeness

This run goes through the SHARED (database-backed) limiter from 093, so it contends for the same
machine-wide slot as the daily worker rather than keeping a private budget. At the default 60s
that is ~2,946 requests ≈ 49 hours, so:

  * Stopping the ops worker first is still the calmer option — sharing the budget means the two
    jobs interleave and BOTH take longer, and the backfill will simply wait its turn.
  * A refusal (netkeiba blocks with a bare HTTP 400) aborts immediately rather than grinding
    through the rest of the list against a source that is already refusing.

Resumable: it re-queries the remaining races each run, so a kill and restart never re-fetches a
race whose prize already landed.

    python scripts/backfill_prize.py --dry-run
    python scripts/backfill_prize.py --min-interval 60 --limit 500
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

from horseracing_db.session import create_db_engine
from horseracing_scrape import robots_cache
from horseracing_scrape.fetch import FetchRefused, HttpFetcher, RobotsDisallowed
from horseracing_scrape.politeness import background_policy
from horseracing_scrape.pipeline import scrape_entries
from horseracing_scrape.urls import entries_url
from sqlalchemy import text
from sqlalchemy.orm import Session

_USER_AGENT = "horseracing-scrape/0.1 (personal use; contact via repo)"

TARGETS_SQL = text("""
SELECT r.race_id, r.race_date
FROM races r
WHERE r.prize_money IS NULL
  AND r.race_date BETWEEN :d0 AND :d1
  AND EXISTS (SELECT 1 FROM race_horses rh WHERE rh.race_id = r.race_id)
ORDER BY r.race_date DESC, r.race_id
""")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_", default="2025-01-01")
    ap.add_argument("--to", dest="to", default=str(dt.date.today()))
    ap.add_argument("--min-interval", type=float, default=60.0,
                    help="seconds between requests (default 60 = the operator's budget)")
    ap.add_argument("--limit", type=int, default=None, help="stop after N races (spread the run)")
    ap.add_argument("--dry-run", action="store_true", help="list the work, send nothing")
    ap.add_argument("--archive-dir", default=None,
                    help="keep the fetched HTML (092) so a future re-parse costs no requests")
    ap.add_argument("--database-url", default=None)
    args = ap.parse_args(argv)

    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )

    engine = create_db_engine()
    with Session(engine) as session:
        rows = session.execute(
            TARGETS_SQL, {"d0": args.from_, "d1": args.to}
        ).all()

    targets = [(r.race_id, r.race_date) for r in rows]
    if args.limit:
        targets = targets[: args.limit]

    hours = len(targets) * args.min_interval / 3600
    print(f"races missing prize in {args.from_}..{args.to}: {len(rows)}")
    print(f"this run: {len(targets)}  ≈ {hours:.1f}h at {args.min_interval:g}s/request")
    if args.dry_run:
        for rid, d in targets[:10]:
            print(f"  {d} {rid}")
        if len(targets) > 10:
            print(f"  ... and {len(targets) - 10} more")
        return 0

    if not targets:
        return 0

    import httpx

    # Go through the SHARED limiter, not just this process's own. A 49-hour run is exactly the
    # thing the daily worker would collide with, and two streams at 60s each is 2 req/min.
    policy = background_policy(min_interval_s=args.min_interval, database_url=args.database_url)
    fetcher = HttpFetcher(
        user_agent=_USER_AGENT, min_interval_s=args.min_interval,
        archive_dir=args.archive_dir,
        client=httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0),
        pre_request=None if policy is None else policy.pre_request,
        on_refusal=None if policy is None else policy.record_refusal,
        robots_cache_store=robots_cache.shared_cache(),
    )

    done = failed = 0
    t0 = time.time()
    with Session(engine) as session:
        for i, (race_id, race_date) in enumerate(targets, 1):
            try:
                summary = scrape_entries(
                    session, urls=[entries_url(race_id)], fetcher=fetcher,
                    scope_value=race_id, complete_profiles_after=False,
                )
            except FetchRefused as exc:
                # The source is refusing. Continuing would spend the rest of the budget being
                # refused — the exact failure the 400-block comment in fetch.py describes.
                print(f"\nABORT at {i}/{len(targets)}: source refused ({exc}). "
                      f"Wait for the cooldown before resuming.", flush=True)
                break
            except RobotsDisallowed as exc:
                print(f"\nABORT: robots disallows {exc}", flush=True)
                break
            except Exception as exc:  # noqa: BLE001 — one bad page must not end the pass
                failed += 1
                print(f"  [{i}/{len(targets)}] {race_id} ERROR {type(exc).__name__}: {exc}",
                      flush=True)
                continue

            got = session.execute(
                text("SELECT prize_money FROM races WHERE race_id = :r"), {"r": race_id}
            ).scalar()
            if got is not None:
                done += 1
            else:
                failed += 1
            if i % 25 == 0 or i == len(targets):
                rate = (time.time() - t0) / i
                left = (len(targets) - i) * rate / 3600
                print(f"  [{i}/{len(targets)}] filled={done} missing={failed} "
                      f"({summary.status}) ~{left:.1f}h left", flush=True)

    print(f"\nfilled={done} still-missing={failed} in {(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
