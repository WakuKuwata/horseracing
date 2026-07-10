"""Operator CLI for live serving (Feature 019). live-serve / list-pending / refresh (050)."""

from __future__ import annotations

import argparse
import datetime

from horseracing_betting.kelly_types import KellyConfig
from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .orchestrate import collect_prospective, list_pending, live_serve, refresh_range


def _default_scrape_fn(session: Session, race_id):
    """Fresh pre-race odds capture for the prospective collection. Returns the CAPTURE completion
    timestamp (now) on success, or None to SKIP (never fall back to stale/closing odds). Uses the
    008 polite netkeiba win-odds scrape; if it fails or is blocked, the race is skipped (the
    instrument only fills with genuinely captured pre-race odds — the operational dependency)."""
    from horseracing_scrape.fetch import PoliteFetcher
    from horseracing_scrape.pipeline import scrape_odds
    from horseracing_scrape.urls import win_odds_url

    try:
        with PoliteFetcher() as fetcher:
            summary = scrape_odds(session, urls=[win_odds_url(race_id)], fetcher=fetcher)
        if getattr(summary, "error_count", 0):
            return None
        return datetime.datetime.now(datetime.UTC)
    except Exception:  # noqa: BLE001 — scrape unavailable ⇒ skip, do not use stale odds
        return None


def _cmd_collect_prospective(session: Session, args) -> int:
    """Feature 065: record prospective win bets on freshly-captured pre-race odds for pending races
    on a date (thin bundle over collect_prospective; scrape/settle stay separate commands)."""
    ids = list_pending(session, date=args.date)
    rep = collect_prospective(
        session, race_ids=ids, scrape_fn=_default_scrape_fn, win_odds_cap=args.win_odds_cap,
    )
    print(f"collect-prospective {args.date}  pending={rep.n_races} generated={rep.generated} "
          f"weak_pretime={rep.weak_pretime}")
    print(f"  skip: not_pending={rep.skip_not_pending} no_odds={rep.skip_no_odds} "
          f"no_run={rep.skip_no_run} exists={rep.skip_exists} post_time={rep.skip_post_time} "
          f"errors={rep.errors}")
    return 0


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _cmd_live_serve(session: Session, args) -> int:
    cfg = KellyConfig(bankroll=args.bankroll, allocation=args.allocation)
    rep = live_serve(
        session, race_id=args.race_id, model_version=args.model_version,
        recommend=not args.no_recommend, cfg=cfg, threshold=args.threshold, top_k=args.top_k,
    )
    if rep.rejected:
        print(f"REJECTED race={rep.race_id}: {rep.reason}")
        for k, (ok, reason) in rep.guards.items():
            print(f"  guard {k:<16} {'ok' if ok else 'FAIL'}  {reason}")
        return 1
    print(f"LIVE race={rep.race_id} ({rep.race_date})  prediction_run={rep.prediction_run_id}")
    print(f"  horses={rep.n_horses}  recommendations={rep.n_recommendations} (Kelly, SHADOW)")
    if rep.recommend_skipped_reason:
        print(f"  recommendations skipped: {rep.recommend_skipped_reason}")
    print(f"  odds_as_of={rep.odds_as_of}  computed_at={rep.computed_at}")
    print("  ※ live Kelly は shadow（記録のみ・実資金執行なし）。cutoff=race_date（004 継承）")
    return 0


def _cmd_list_pending(session: Session, args) -> int:
    ids = list_pending(session, date=args.date)
    print(f"result-pending races on {args.date}: {len(ids)}")
    for rid in ids:
        print(f"  {rid}")
    return 0


def _cmd_refresh(session: Session, args) -> int:
    """Feature 050: one-command product update — predict backfill THEN recommend backfill."""
    rep = refresh_range(
        session, date_from=args.from_, date_to=args.to, force=args.force,
        use_materialized=args.use_materialized,
        materialized_path=args.materialized_path if args.use_materialized else None,
    )
    print(f"refresh {rep.date_from}..{rep.date_to}")
    if rep.predict is not None:
        p = rep.predict
        print(f"  predict:   generated={p['generated']} skip_exists={p['skip_exists']} "
              f"skip_no_started={p['skip_no_started']} error_days={p['error_days']}")
    else:
        print(f"  predict:   FAILED — {rep.predict_error}")
    if rep.recommend is not None:
        r = rep.recommend
        print(f"  recommend: races={r['races']} generated={r['generated']} "
              f"topped_up={r['topped_up']} skip_exists={r['skip_exists']} "
              f"skip_no_run={r['skip_no_run']} skip_no_odds={r['skip_no_odds']} "
              f"error={r['error']}")
    else:
        print(f"  recommend: FAILED — {rep.recommend_error}")
    return 0 if (rep.predict_error is None and rep.recommend_error is None) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="live")
    sub = parser.add_subparsers(dest="command", required=True)

    ls = sub.add_parser("live-serve", help="predict + recommend for an upcoming (pending) race")
    ls.add_argument("race_id")
    ls.add_argument("--model-version", default=None)
    ls.add_argument("--no-recommend", action="store_true")
    ls.add_argument("--bankroll", type=float, default=100.0)
    ls.add_argument("--allocation", choices=["exact", "heuristic"], default="exact")
    ls.add_argument("--threshold", type=float, default=1.0)
    ls.add_argument("--top-k", type=int, default=5)
    ls.add_argument("--database-url", default=None)

    lp = sub.add_parser("list-pending", help="list valid result-pending races on a date")
    lp.add_argument("--date", type=_parse_date, required=True)
    lp.add_argument("--database-url", default=None)

    rf = sub.add_parser("refresh",
                        help="one-command product update: predict backfill → recommend backfill "
                             "over a date range (050)")
    rf.add_argument("--from", dest="from_", type=_parse_date, required=True)
    rf.add_argument("--to", type=_parse_date, required=True)
    rf.add_argument("--force", action="store_true",
                    help="re-generate predictions (044 append-only); recommendations stay "
                         "group-wise idempotent")
    rf.add_argument("--use-materialized", action="store_true",
                    help="055: prediction stage reads as-of features from the 025 parquet "
                         "(bit-parity, fail-closed; recommend stage builds no features)")
    rf.add_argument("--materialized-path", default="../artifacts/features.parquet")
    rf.add_argument("--database-url", default=None)

    cp = sub.add_parser("collect-prospective",
                        help="065: record prospective win bets on freshly-captured pre-race odds "
                             "for pending races on a date (fills the shadow-log)")
    cp.add_argument("--date", type=_parse_date, required=True)
    cp.add_argument("--win-odds-cap", dest="win_odds_cap", type=float, default=None,
                    help="064: optional win odds cap policy for the prospective bets")
    cp.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "live-serve":
            return _cmd_live_serve(session, args)
        if args.command == "list-pending":
            return _cmd_list_pending(session, args)
        if args.command == "refresh":
            return _cmd_refresh(session, args)
        if args.command == "collect-prospective":
            return _cmd_collect_prospective(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
