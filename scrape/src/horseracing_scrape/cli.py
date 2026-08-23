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

from . import robots_cache
from .fetch import HttpFetcher
from .pipeline import (
    complete_corner_orders,
    complete_profiles,
    discover_races,
    scrape_entries,
    scrape_exotic_odds,
    scrape_exotic_quotes,
    scrape_laps,
    scrape_odds,
    scrape_results,
)
from .politeness import background_policy
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


def _make_fetcher(
    min_interval: float,
    cache_dir: str | None,
    database_url: str | None = None,
    archive_dir: str | None = None,
) -> HttpFetcher:
    """The fetcher every ingest CLI shares.

    Attaching the DB-backed policy here is what makes "1 request per minute" true of the MACHINE
    rather than of one object: `_rate_limit` keys on the fetcher instance and the hostname, so the
    worker's per-iteration fetcher starts with an empty dict, race.netkeiba and db.netkeiba each
    got a full budget, and a second process shared nothing at all.
    """
    import httpx

    # --archive-dir wins over --cache-dir (which defaults to a path on several subcommands):
    # the two are mutually exclusive by construction, and an archive is only meaningful when
    # every page is actually fetched rather than replayed from a cache.
    if archive_dir:
        cache_dir = None

    policy = background_policy(min_interval_s=min_interval, database_url=database_url)
    return HttpFetcher(
        user_agent=_USER_AGENT, min_interval_s=min_interval, cache_dir=cache_dir,
        archive_dir=archive_dir,
        client=httpx.Client(headers={"User-Agent": _USER_AGENT}, timeout=20.0),
        pre_request=None if policy is None else policy.pre_request,
        on_refusal=None if policy is None else policy.record_refusal,
        # One request per origin, ever — instead of a robots round-trip that used to bypass the
        # limiter entirely on every fetch.
        robots_cache_store=robots_cache.shared_cache(),
    )


def _fetcher_for(args, cache_dir: str | None) -> HttpFetcher:
    """Every subcommand's fetcher. Subcommands that take no --database-url still coordinate,
    through whatever DATABASE_URL the environment names."""
    return _make_fetcher(
        args.min_interval, cache_dir,
        getattr(args, "database_url", None), getattr(args, "archive_dir", None),
    )


def _capture_fixture(args) -> int:
    url_fn, ext, use_cache, id_arg = _CAPTURE[args.kind]
    ident = getattr(args, id_arg, None)
    if not ident:
        raise SystemExit(f"--{id_arg.replace('_', '-')} is required for kind={args.kind}")
    url = url_fn(ident)
    fetcher = _fetcher_for(args, None)  # capture never uses a stale cache
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
        p.add_argument("--cache-dir", default=None,
        help="read-through cache (opt-in). A page fetched while a race was still pending "
             "would be replayed forever, so this stays OFF for anything not yet settled")
        p.add_argument("--archive-dir", default=None,
            help="gzip a copy of every fetched page here "
                 "(write-only archive, not a cache; disables --cache-dir)")
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
    cp.add_argument("--cache-dir", default=None,
        help="read-through cache (opt-in). A page fetched while a race was still pending "
             "would be replayed forever, so this stays OFF for anything not yet settled")
    cp.add_argument("--archive-dir", default=None,
        help="gzip a copy of every fetched page here "
                 "(write-only archive, not a cache; disables --cache-dir)")
    cp.add_argument("--min-interval", type=float, default=1.0)
    cp.add_argument("--database-url", default=None)

    cc = sub.add_parser("complete-corners",
                        help="re-fetch result pages of finished races whose 通過順 is still NULL "
                             "(race-night pages lack it; fill-NULL-only, 1 request per race)")
    cc.add_argument("--race-id", action="append", default=None,
                    help="JRA-VAN 12-digit race_id (repeat); omit to select by NULL corners")
    cc.add_argument("--older-than-days", type=int, default=1,
                    help="only races at least this many days old (source publishes corners later)")
    cc.add_argument("--limit", type=int, default=None, help="max races this run")
    cc.add_argument("--archive-dir", default=None,
        help="gzip a copy of every fetched page here (write-only archive, not a cache)")
    cc.add_argument("--min-interval", type=float, default=1.0)
    cc.add_argument("--database-url", default=None)

    # ⑤ sectional lap backfill (034): by explicit race_id(s) or a date range of races missing laps
    eq = sub.add_parser(
        "scrape-exotic-quotes",
        help="PRE-RACE exotic price grid (one request PER BET TYPE per race)")
    eq.add_argument("--race-id", action="append", dest="race_ids", required=True,
                    help="repeatable")
    eq.add_argument("--bet-type", action="append", dest="bet_types",
                    default=None,
                    help="repeatable; default quinella,wide,trio "
                         "(trifecta is a 4,896-combination grid at 18 runners)")
    eq.add_argument("--min-interval", type=float, default=1.0)
    eq.add_argument("--database-url", default=None)

    qc = sub.add_parser(
        "exotic-quote-coverage",
        help="how many races that RAN have a captured pre-race price grid (silent-stop guard)")
    qc.add_argument("--days", type=int, default=30)
    qc.add_argument("--database-url", default=None)

    sl = sub.add_parser("scrape-laps",
                        help="ingest race-level sectional laps (034) from db.netkeiba race pages")
    sl.add_argument("--race-id", action="append", default=None,
                    help="JRA-VAN 12-digit race_id (repeat); omit to use --from/--to")
    sl.add_argument("--from", dest="from_", default=None, help="race_date >= (YYYY-MM-DD)")
    sl.add_argument("--to", dest="to", default=None, help="race_date <= (YYYY-MM-DD)")
    sl.add_argument("--limit", type=int, default=None, help="max races this run")
    sl.add_argument("--cache-dir", default=None,
        help="read-through cache (opt-in). A page fetched while a race was still pending "
             "would be replayed forever, so this stays OFF for anything not yet settled")
    sl.add_argument("--archive-dir", default=None,
        help="gzip a copy of every fetched page here "
                 "(write-only archive, not a cache; disables --cache-dir)")
    sl.add_argument("--min-interval", type=float, default=1.0)
    sl.add_argument("--database-url", default=None)

    # Feature 067: identity resolution + physical split repair (operator, idempotent, dry-run)
    ri = sub.add_parser("resolve-identities",
                        help="promote UNMAPPED netkeiba id_mappings to MAPPED/CONFLICT by identity")
    ri.add_argument("--entity", choices=["horse", "jockey", "trainer", "all"], default="all")
    ri.add_argument("--dry-run", action="store_true")
    ri.add_argument("--database-url", default=None)

    rs = sub.add_parser("repair-splits",
                        help="physically re-key MAPPED surrogates to canonical + delete orphans")
    rs.add_argument("--entity", choices=["horse", "jockey", "trainer", "all"], default="all")
    rs.add_argument("--dry-run", action="store_true")
    rs.add_argument("--limit", type=int, default=None, help="max surrogate->canonical pairs")
    rs.add_argument("--database-url", default=None)

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
        fetcher = _fetcher_for(args, None)
        listing = discover_races(fetcher, args.date)
        for rid in listing.race_ids:
            if args.urls:
                print(f"{rid}\t{entries_url(rid)}\t{result_url(rid)}\t{win_odds_url(rid)}")
            else:
                print(rid)
        print(f"# {len(listing.race_ids)} races for {listing.kaisai_date}")
        return 0

    if args.command == "resolve-identities":
        from .repair import resolve_identities
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            rep = resolve_identities(session, entity=args.entity, dry_run=args.dry_run)
        for ent in sorted(rep.resolved):
            print(f"{ent}: resolved={rep.resolved.get(ent, 0)} "
                  f"conflict={rep.conflicts.get(ent, 0)} "
                  f"insufficient={rep.insufficient.get(ent, 0)}")
            for ex in rep.examples.get(ent, []):
                print(f"    {ex}")
        print(f"# resolve-identities dry_run={rep.dry_run}")
        return 0

    if args.command == "repair-splits":
        from .repair import repair_splits
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            rep = repair_splits(session, entity=args.entity, dry_run=args.dry_run, limit=args.limit)
        print(f"pairs_processed={rep.pairs_processed} affected_from={rep.affected_from}")
        print(f"rekeyed_rows={rep.rekeyed_rows}")
        print(f"orphans_deleted={rep.orphans_deleted} collisions={rep.collisions} held={rep.held}")
        if rep.errors:
            print(f"errors({len(rep.errors)}): {rep.errors[:5]}")
        print(f"# repair-splits dry_run={rep.dry_run}")
        return 0 if not rep.errors else 1

    if args.command == "scrape-exotic-quotes":
        fetcher = _fetcher_for(args, None)  # volatile: never cached
        bet_types = args.bet_types or ["quinella", "wide", "trio"]
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            summary = scrape_exotic_quotes(
                session, race_ids=args.race_ids, bet_types=bet_types, fetcher=fetcher,
                scope_value=f"{len(args.race_ids)} races x {','.join(bet_types)}",
            )
        print(
            f"{summary.job_type}: status={summary.status} processed={summary.processed} "
            f"written={summary.written} skipped={summary.skipped} errors={summary.errors}"
        )
        return 0

    if args.command == "exotic-quote-coverage":
        # These grids cannot be recovered: a race that runs uncaptured is gone. The failure mode is
        # silent (a stale worker, a pool that never opened, a renamed field), so the only defence
        # is looking at the coverage number regularly.
        from sqlalchemy import text as _t
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            rows = session.execute(_t("""
                SELECT r.race_date,
                       count(*) AS races,
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM exotic_quotes q WHERE q.race_id = r.race_id)) AS captured
                FROM races r
                WHERE r.race_date >= current_date - :d
                  AND EXISTS (SELECT 1 FROM race_results x WHERE x.race_id = r.race_id)
                GROUP BY r.race_date ORDER BY r.race_date DESC
            """), {"d": args.days}).all()
        if not rows:
            print("no completed races in the window")
            return 0
        print(f"{'date':<12}{'races':>7}{'captured':>10}{'coverage':>10}")
        tot = cap = 0
        for day, races, captured in rows:
            tot += races
            cap += captured
            flag = "" if captured else "   <- NOTHING CAPTURED"
            print(f"{str(day):<12}{races:>7}{captured:>10}{captured / races:>9.0%}{flag}")
        print(f"{'total':<12}{tot:>7}{cap:>10}{(cap / tot if tot else 0):>9.0%}")
        return 0

    if args.command == "scrape-laps":
        fetcher = _fetcher_for(args, args.cache_dir)
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            race_ids = args.race_id
            if not race_ids:  # date-range backfill: races missing a race_laps row
                from sqlalchemy import text as _text
                q = ("select r.race_id from races r left join race_laps l on l.race_id=r.race_id "
                     "where l.race_id is null")
                params = {}
                if args.from_:
                    q += " and r.race_date >= :f"
                    params["f"] = args.from_
                if args.to:
                    q += " and r.race_date <= :t"
                    params["t"] = args.to
                q += " order by r.race_id"
                if args.limit:
                    q += f" limit {int(args.limit)}"
                race_ids = [row[0] for row in session.execute(_text(q), params).fetchall()]
            scope = (args.race_id[0] if args.race_id
                     else f"{args.from_ or '..'}..{args.to or '..'}")
            summary = scrape_laps(session, race_ids=race_ids, fetcher=fetcher, scope_value=scope)
        print(
            f"{summary.job_type}: status={summary.status} processed={summary.processed} "
            f"written={summary.written} skipped={summary.skipped} errors={summary.errors}"
        )
        return 0 if summary.status != "failed" else 1

    if args.command == "complete-corners":
        fetcher = _fetcher_for(args, None)  # never a read-through cache: we WANT the fresh page
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            summary = complete_corner_orders(
                session, fetcher=fetcher, older_than_days=args.older_than_days,
                limit=args.limit, race_ids=args.race_id,
            )
        print(
            f"{summary.job_type}: status={summary.status} processed={summary.processed} "
            f"written={summary.written} skipped={summary.skipped} errors={summary.errors}"
        )
        return 0 if summary.status != "failed" else 1

    if args.command == "complete-profiles":
        fetcher = _fetcher_for(args, args.cache_dir)
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
    fetcher = _fetcher_for(args, args.cache_dir)
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
