"""Operator CLI: show (top-K combination probabilities) / calibrate (PL vs baseline)."""

from __future__ import annotations

import argparse
import datetime

from horseracing_db.models import PredictionRun, RacePrediction
from horseracing_db.session import create_db_engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from .calibration import _latest_run_predictions, evaluate_calibration
from .consistency import check_joint_consistency
from .engine import joint_probabilities


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _run_predictions(session: Session, prediction_run_id) -> tuple[str, dict[str, float]]:
    run = session.get(PredictionRun, prediction_run_id)
    if run is None:
        raise SystemExit(f"prediction_run {prediction_run_id} not found")
    rows = session.execute(
        select(RacePrediction.horse_id, RacePrediction.win_prob).where(
            RacePrediction.prediction_run_id == prediction_run_id
        )
    ).all()
    return run.race_id, {h: float(w) for h, w in rows if w is not None}


def _cmd_show(session: Session, args) -> int:
    if args.prediction_run:
        race_id, win_probs = _run_predictions(session, args.prediction_run)
    else:
        race_id, win_probs = args.race_id, _latest_run_predictions(session, args.race_id)
    if len(win_probs) < 2:
        raise SystemExit(f"no usable predictions for race {race_id}")

    jp = joint_probabilities(win_probs)
    check_joint_consistency(jp)
    print(f"race={race_id} horses={len(win_probs)}  [consistency OK]")

    def top(d, label):
        items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
        print(f"-- {label} (top {args.top}) --")
        for key, p in items:
            name = key if isinstance(key, str) else "-".join(sorted(key)) if isinstance(
                key, frozenset) else "→".join(key)
            print(f"   {name}: {p:.5f}")

    top(jp.win, "単勝/win")
    if jp.place is not None:
        top(jp.place, "複勝/place")
    top(jp.exacta, "馬単/exacta")
    top(jp.quinella, "馬連/quinella")
    if jp.wide is not None:
        top(jp.wide, "ワイド/wide")
    if jp.trio:
        top(jp.trio, "三連複/trio")
    if jp.trifecta:
        top(jp.trifecta, "三連単/trifecta")
    return 0


def _cmd_calibrate(session: Session, args) -> int:
    reports = evaluate_calibration(
        session, start_date=args.from_, end_date=args.to, bet_type=args.bet_type
    )
    any_r = next(iter(reports.values()))
    print(f"calibration {args.from_}..{args.to} bet_type={args.bet_type} races={any_r.n_races}")
    print(f"{'strategy':<22} {'nll':>10} {'brier':>10}")
    for name in ("plackett_luce", "independent_product"):
        r = reports[name]
        print(f"{name:<22} {r.nll:>10.5f} {r.brier:>10.5f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_probability")
    sub = parser.add_subparsers(dest="command", required=True)

    sh = sub.add_parser("show", help="top-K combination probabilities for a race")
    sh.add_argument("--prediction-run", default=None)
    sh.add_argument("--race-id", default=None)
    sh.add_argument("--top", type=int, default=10)
    sh.add_argument("--database-url", default=None)

    ca = sub.add_parser("calibrate", help="calibration: Plackett-Luce vs independent-product")
    ca.add_argument("--from", dest="from_", type=_parse_date, required=True)
    ca.add_argument("--to", type=_parse_date, required=True)
    ca.add_argument("--bet-type", choices=["exacta", "trifecta"], default="exacta")
    ca.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "show":
            if (args.prediction_run is None) == (args.race_id is None):
                parser.error("exactly one of --prediction-run or --race-id is required")
            return _cmd_show(session, args)
        if args.command == "calibrate":
            return _cmd_calibrate(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
