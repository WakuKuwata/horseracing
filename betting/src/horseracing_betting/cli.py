"""Operator CLI: recommend (race/run) and backtest (period) — quickstart.md."""

from __future__ import annotations

import argparse
import datetime

from horseracing_db.models import PredictionRun
from horseracing_db.session import create_db_engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from .backtest import run_backtest
from .recommend import DEFAULT_STAKE, DEFAULT_THRESHOLD, generate_recommendations


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _resolve_run(session: Session, race_id: str):
    run = session.scalars(
        select(PredictionRun)
        .where(PredictionRun.race_id == race_id)
        .order_by(PredictionRun.computed_at.desc())
    ).first()
    if run is None:
        raise SystemExit(f"no prediction_run for race {race_id}; run serving first")
    return run.prediction_run_id


def _cmd_recommend(session: Session, args) -> int:
    run_id = args.prediction_run or _resolve_run(session, args.race_id)
    ids = generate_recommendations(
        session, prediction_run_id=run_id, threshold=args.threshold, stake=args.stake
    )
    print(f"prediction_run={run_id} recommendations={len(ids)} (bet_type=win)")
    return 0


def _cmd_backtest(session: Session, args) -> int:
    reports = run_backtest(
        session, start_date=args.from_, end_date=args.to,
        model_version=args.model_version, threshold=args.threshold, stake=args.stake,
    )
    any_report = next(iter(reports.values()))
    tag = "PSEUDO" + (" / IN-SAMPLE" if any_report.in_sample else "")
    print(f"backtest {args.from_}..{args.to}  [{tag}]  races={any_report.n_races}")
    print(f"{'strategy':<10} {'recovery':>9} {'hit':>7} {'skip':>7} {'bets':>6} {'maxDD':>10}")
    for name in ("ev", "favorite", "uniform"):
        r = reports[name]
        print(
            f"{name:<10} {r.recovery_rate:>9.3f} {r.hit_rate:>7.3f} {r.skip_rate:>7.3f} "
            f"{r.n_bets:>6} {r.max_drawdown:>10.0f} (streak {r.max_losing_streak})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_betting")
    sub = parser.add_subparsers(dest="command", required=True)

    rc = sub.add_parser("recommend", help="generate single-win EV recommendations")
    rc.add_argument("--prediction-run", default=None)
    rc.add_argument("--race-id", default=None)
    rc.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    rc.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    rc.add_argument("--database-url", default=None)

    bt = sub.add_parser("backtest", help="pseudo-ROI backtest vs ROI baselines")
    bt.add_argument("--from", dest="from_", type=_parse_date, required=True)
    bt.add_argument("--to", type=_parse_date, required=True)
    bt.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    bt.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    bt.add_argument("--model-version", default=None)
    bt.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "recommend":
            if (args.prediction_run is None) == (args.race_id is None):
                parser.error("exactly one of --prediction-run or --race-id is required")
            return _cmd_recommend(session, args)
        if args.command == "backtest":
            return _cmd_backtest(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
