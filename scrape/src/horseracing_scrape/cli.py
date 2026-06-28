"""Operator CLI: scrape-{entries,odds,results,exotic-odds} + capture-fixture.

The operator supplies netkeiba page URL(s) with --url (repeatable). The JRA-VAN race_id is
derived from the page content / URL — pages whose race_id can't be constructed are skipped (no
fake IDs). A real polite HttpFetcher is used (tests inject FixtureFetcher via the pipeline funcs).

``capture-fixture`` is a one-off helper (Feature 022) that politely fetches a single page by race_id
and saves the raw payload + a manifest entry (url/fetched_at/sha256) for use as a network-free test
fixture. entries/results are HTML; odds is the win-odds JSON fetched no-cache.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path

from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .fetch import HttpFetcher
from .pipeline import scrape_entries, scrape_exotic_odds, scrape_odds, scrape_results
from .urls import entries_url, result_url, win_odds_url

_USER_AGENT = "horseracing-scrape/0.1 (personal use; contact via repo)"
_COMMANDS = {"scrape-entries": scrape_entries, "scrape-odds": scrape_odds,
             "scrape-results": scrape_results, "scrape-exotic-odds": scrape_exotic_odds}
_CAPTURE = {
    "entries": (entries_url, "html", True),
    "results": (result_url, "html", True),
    "odds": (win_odds_url, "json", False),  # odds fetched no-cache (single-latest, constitution V)
}


def _make_fetcher(min_interval: float, cache_dir: str | None) -> HttpFetcher:
    import httpx

    return HttpFetcher(
        user_agent=_USER_AGENT, min_interval_s=min_interval, cache_dir=cache_dir,
        client=httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0),
    )


def _capture_fixture(args) -> int:
    url_fn, ext, use_cache = _CAPTURE[args.kind]
    url = url_fn(args.race_id)
    fetcher = _make_fetcher(args.min_interval, None)  # capture never uses a stale cache
    payload = fetcher.get(url, use_cache=use_cache)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fname = f"{args.kind}_{args.race_id}.{ext}"
    (out / fname).write_text(payload, encoding="utf-8")

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"fixtures": []}
    manifest["fixtures"] = [f for f in manifest["fixtures"] if f.get("file") != fname]
    manifest["fixtures"].append({
        "page_kind": args.kind, "file": fname, "url": url, "race_id": args.race_id,
        "fetched_at": datetime.date.today().isoformat(),
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "trim_note": "raw capture (untrimmed)",
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"captured {args.kind} {args.race_id} -> {out / fname} ({len(payload)} bytes)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_scrape")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in _COMMANDS:
        p = sub.add_parser(name, help=f"{name} from netkeiba page URL(s)")
        p.add_argument("--url", action="append", required=True, help="netkeiba page URL (repeat)")
        p.add_argument("--cache-dir", default=".scrape_cache")
        p.add_argument("--min-interval", type=float, default=1.0)
        p.add_argument("--database-url", default=None)

    cap = sub.add_parser("capture-fixture", help="one-off: save a real netkeiba page as a fixture")
    cap.add_argument("--race-id", required=True, help="JRA-VAN 12-digit race_id")
    cap.add_argument("--kind", required=True, choices=list(_CAPTURE))
    cap.add_argument("--out", default="scrape/tests/fixtures/real")
    cap.add_argument("--min-interval", type=float, default=1.0)

    args = parser.parse_args(argv)

    if args.command == "capture-fixture":
        return _capture_fixture(args)

    fn = _COMMANDS[args.command]
    fetcher = _make_fetcher(args.min_interval, args.cache_dir)
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
