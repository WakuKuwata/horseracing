"""Operator CLI for live serving (Feature 019). live-serve / list-pending."""

from __future__ import annotations

import argparse
import datetime

from horseracing_betting.kelly_types import KellyConfig
from horseracing_db.session import create_db_engine
from sqlalchemy.orm import Session

from .orchestrate import list_pending, live_serve


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

    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "live-serve":
            return _cmd_live_serve(session, args)
        if args.command == "list-pending":
            return _cmd_list_pending(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
