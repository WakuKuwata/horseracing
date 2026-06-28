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
from .pipeline import (
    complete_profiles,
    discover_races,
    scrape_entries,
    scrape_exotic_odds,
    scrape_odds,
    scrape_results,
)
from .urls import (
    entries_url,
    horse_pedigree_url,
    horse_profile_url,
    race_list_url,
    result_url,
    win_odds_url,
)

_USER_AGENT = "horseracing-scrape/0.1 (personal use; contact via repo)"
_COMMANDS = {"scrape-entries": scrape_entries, "scrape-odds": scrape_odds,
             "scrape-results": scrape_results, "scrape-exotic-odds": scrape_exotic_odds}
#: capture-fixture kinds: (url builder, file ext, use_cache, id-arg name on argparse Namespace)
_CAPTURE = {
    "entries": (entries_url, "html", True, "race_id"),
    "results": (result_url, "html", True, "race_id"),
    "odds": (win_odds_url, "json", False, "race_id"),  # no-cache (single-latest, constitution V)
    "race_list": (race_list_url, "html", False, "date"),       # day discovery fragment (③)
    "horse_profile": (horse_profile_url, "html", True, "horse_id"),  # identity (④)
    "pedigree": (horse_pedigree_url, "html", True, "horse_id"),      # server-rendered blood_table
}


def _make_fetcher(min_interval: float, cache_dir: str | None) -> HttpFetcher:
    import httpx

    return HttpFetcher(
        user_agent=_USER_AGENT, min_interval_s=min_interval, cache_dir=cache_dir,
        client=httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0),
    )


def _capture_fixture(args) -> int:
    url_fn, ext, use_cache, id_arg = _CAPTURE[args.kind]
    ident = getattr(args, id_arg, None)
    if not ident:
        raise SystemExit(f"--{id_arg.replace('_', '-')} is required for kind={args.kind}")
    url = url_fn(ident)
    fetcher = _make_fetcher(args.min_interval, None)  # capture never uses a stale cache
    payload = fetcher.get(url, use_cache=use_cache)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fname = f"{args.kind}_{ident}.{ext}"
    (out / fname).write_text(payload, encoding="utf-8")

    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"fixtures": []}
    manifest["fixtures"] = [f for f in manifest["fixtures"] if f.get("file") != fname]
    manifest["fixtures"].append({
        "page_kind": args.kind, "file": fname, "url": url, id_arg: ident,
        "fetched_at": datetime.date.today().isoformat(),
        "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "trim_note": "raw capture (untrimmed)",
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"captured {args.kind} {ident} -> {out / fname} ({len(payload)} bytes)")
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
        if name == "scrape-entries":
            p.add_argument("--no-complete-profiles", action="store_true",
                           help="skip the automatic horse identity/pedigree completion")

    # ③ day discovery: list a day's race_ids (read-only; operator feeds them to scrape-*)
    lr = sub.add_parser("list-races", help="list a day's race_ids from netkeiba (kaisai_date)")
    lr.add_argument("--date", required=True, help="開催日 YYYYMMDD (or YYYY-MM-DD)")
    lr.add_argument("--min-interval", type=float, default=1.0)
    lr.add_argument("--urls", action="store_true", help="also print entries/result/odds URLs")

    # ④ opt-in profile completion: fill leak-safe identity/pedigree for surrogate horses
    cp = sub.add_parser("complete-profiles",
                        help="opt-in: fill leak-safe horse pedigree/identity from db.netkeiba.com")
    cp.add_argument("--horse-id", action="append", default=None,
                    help="netkeiba horse id to complete (repeat); default = surrogate horses")
    cp.add_argument("--limit", type=int, default=None, help="max horses to fetch this run")
    cp.add_argument("--cache-dir", default=".scrape_cache")
    cp.add_argument("--min-interval", type=float, default=1.0)
    cp.add_argument("--database-url", default=None)

    cap = sub.add_parser("capture-fixture", help="one-off: save a real netkeiba page as a fixture")
    cap.add_argument("--kind", required=True, choices=list(_CAPTURE))
    cap.add_argument("--race-id", help="JRA-VAN 12-digit race_id (entries/results/odds)")
    cap.add_argument("--date", help="開催日 YYYYMMDD (race_list)")
    cap.add_argument("--horse-id", dest="horse_id", help="netkeiba horse id (horse_profile)")
    cap.add_argument("--out", default="scrape/tests/fixtures/real")
    cap.add_argument("--min-interval", type=float, default=1.0)

    args = parser.parse_args(argv)

    if args.command == "capture-fixture":
        return _capture_fixture(args)

    if args.command == "list-races":
        fetcher = _make_fetcher(args.min_interval, None)
        listing = discover_races(fetcher, args.date)
        for rid in listing.race_ids:
            if args.urls:
                print(f"{rid}\t{entries_url(rid)}\t{result_url(rid)}\t{win_odds_url(rid)}")
            else:
                print(rid)
        print(f"# {len(listing.race_ids)} races for {listing.kaisai_date}")
        return 0

    if args.command == "complete-profiles":
        fetcher = _make_fetcher(args.min_interval, args.cache_dir)
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            summary = complete_profiles(
                session, fetcher=fetcher, netkeiba_horse_ids=args.horse_id, limit=args.limit
            )
        print(
            f"{summary.job_type}: status={summary.status} processed={summary.processed} "
            f"written={summary.written} skipped={summary.skipped} errors={summary.errors}"
        )
        return 0 if summary.status != "failed" else 1

    fn = _COMMANDS[args.command]
    fetcher = _make_fetcher(args.min_interval, args.cache_dir)
    engine = create_db_engine(args.database_url)
    kwargs = {"urls": args.url, "fetcher": fetcher, "scope_value": args.url[0]}
    if args.command == "scrape-entries":
        kwargs["complete_profiles_after"] = not args.no_complete_profiles
    with Session(engine) as session:
        summary = fn(session, **kwargs)
    print(
        f"{summary.job_type}: status={summary.status} processed={summary.processed} "
        f"written={summary.written} skipped={summary.skipped} errors={summary.errors}"
    )
    return 0 if summary.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
