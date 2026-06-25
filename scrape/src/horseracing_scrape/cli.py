"""Operator CLI: scrape-entries / scrape-odds / scrape-results (quickstart.md).

The operator supplies netkeiba page URL(s) with --url (repeatable). The JRA-VAN race_id is
derived from the page content (build_race_id) — pages whose race_id can't be constructed are
skipped (no fake IDs). A real polite HttpFetcher is used unless --no-fetch-... (tests inject
FixtureFetcher directly via the pipeline functions).
"""

from __future__ import annotations

import argparse

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .fetch import HttpFetcher
from .pipeline import scrape_entries, scrape_exotic_odds, scrape_odds, scrape_results

_USER_AGENT = "horseracing-scrape/0.1 (personal use; contact via repo)"
_COMMANDS = {"scrape-entries": scrape_entries, "scrape-odds": scrape_odds,
             "scrape-results": scrape_results, "scrape-exotic-odds": scrape_exotic_odds}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_scrape")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in _COMMANDS:
        p = sub.add_parser(name, help=f"{name} from netkeiba page URL(s)")
        p.add_argument("--url", action="append", required=True, help="netkeiba page URL (repeat)")
        p.add_argument("--cache-dir", default=".scrape_cache")
        p.add_argument("--min-interval", type=float, default=1.0)
        p.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    fn = _COMMANDS[args.command]
    import httpx

    fetcher = HttpFetcher(
        user_agent=_USER_AGENT, min_interval_s=args.min_interval, cache_dir=args.cache_dir,
        client=httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0),
    )
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        summary = fn(session, urls=args.url, fetcher=fetcher, scope_value=args.url[0])
    print(
        f"{summary.job_type}: status={summary.status} processed={summary.processed} "
        f"written={summary.written} skipped={summary.skipped} errors={summary.errors}"
    )
    return 0 if summary.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
