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


def _race_win_odds(session: Session, race_id: str) -> dict[str, float]:
    from horseracing_db.enums import EntryStatus
    from horseracing_db.models import RaceHorse
    return {
        hid: float(o)
        for hid, o in session.execute(
            select(RaceHorse.horse_id, RaceHorse.odds)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        ).all()
        if o is not None and float(o) > 0.0
    }


def _cmd_estimate_odds(session: Session, args) -> int:
    from .market_odds import estimate_market_odds
    win_odds = _race_win_odds(session, args.race_id)
    if len(win_odds) < 2:
        raise SystemExit(f"no usable win odds for race {args.race_id}")
    eo = estimate_market_odds(win_odds)
    print(f"race={args.race_id} horses={len(win_odds)}  [推定 estimated market odds (pseudo)]")
    print(f"payout_rates={eo.payout_rates}")

    def top(d, label):
        if d is None:
            return
        items = [(k, v) for k, v in d.items() if v is not None]
        items.sort(key=lambda kv: kv[1])  # lowest odds = most likely
        print(f"-- {label} (top {args.top}) --")
        for key, o in items[: args.top]:
            name = key if isinstance(key, str) else (
                "-".join(sorted(key)) if isinstance(key, frozenset) else "→".join(key))
            print(f"   {name}: {o:.1f}")

    for d, label in [
        (eo.win, "単勝/win"), (eo.place, "複勝/place"), (eo.exacta, "馬単/exacta"),
        (eo.quinella, "馬連/quinella"), (eo.wide, "ワイド/wide"),
        (eo.trio, "三連複/trio"), (eo.trifecta, "三連単/trifecta"),
    ]:
        top(d, label)
    return 0


def _cmd_validate_odds(session: Session, args) -> int:
    from .market_calibration import evaluate_market_odds
    rec, qcal = evaluate_market_odds(session, start_date=args.from_, end_date=args.to)
    print(f"validate-odds {args.from_}..{args.to}  [PSEUDO 疑似評価 (estimated market odds)]")
    print(f"win-odds recovery: races={rec.n_races} mean|log(R·S)|={rec.mean_abs_log_ratio:.5f} "
          f"mean|hat/odds-1|={rec.mean_abs_rel_error:.5f}")
    print(f"q calibration:     races={qcal.n_races} nll={qcal.nll:.5f} brier={qcal.brier:.5f}")
    return 0


def _cmd_fl_fit(session: Session, args) -> int:
    from .fl_bias import fit_fl_calibrator, load_samples
    samples = load_samples(session, date_from=args.train_from, date_to=args.train_to)
    cal = fit_fl_calibrator([(wo, w) for _, _, wo, w in samples], method=args.method,
                            train_window=(args.train_from, args.train_to))
    print(f"fl-fit method={cal.method}  [market q→q' calibrator]")
    print(f"  gamma={cal.params.get('gamma'):.5f}  window={args.train_from}..{args.train_to}")
    print(f"  n_races={cal.n_races} n_informative={cal.n_samples} "
          f"q_range=({cal.odds_range[0]:.4f},{cal.odds_range[1]:.4f}) sufficient={cal.sufficient}")
    return 0


def _cmd_calibrate_eval(session: Session, args) -> int:
    from .model_calibration import evaluate_calibration_db
    cal, rep, joint = evaluate_calibration_db(
        session, date_from=args.from_, date_to=args.to, method=args.method,
        min_races=args.min_races, min_wins=args.min_wins, train_frac=args.train_frac,
        base_model_version=args.model_version,
    )
    print(f"calibrate-eval method={cal.method}  [model p→p'; compare=power vs identity]")
    print(f"  gamma={cal.params.get('gamma'):.5f}  sufficient={cal.sufficient}  "
          f"n_train={cal.n_races} n_info={cal.n_samples}")
    print(f"  eval n_races={rep.n_races} dead_heat_excluded={rep.n_dead_heat_excluded}")
    print(f"  {'metric':<14}{'raw p':>12}{'cal p_prime':>14}")
    print(f"  {'NLL(主)':<14}{rep.nll_p:>12.4f}{rep.nll_pp:>14.4f}")
    print(f"  {'Brier(主)':<14}{rep.brier_p:>12.4f}{rep.brier_pp:>14.4f}")
    print(f"  {'ECE(補助)':<14}{rep.ece_p:>12.4f}{rep.ece_pp:>14.4f}")
    print(f"  {'rel.slope':<14}{rep.reliability_slope_p:>12.4f}{rep.reliability_slope_pp:>14.4f}")
    print(f"  {'top ovr/und':<14}{rep.over_under_top_p:>12.4f}{rep.over_under_top_pp:>14.4f}")
    print(f"  {'cal-in-large':<14}{rep.cal_in_large_p:>12.4f}{rep.cal_in_large_pp:>14.4f}")
    print(f"  marginal improved (NLL): {rep.improved}")
    print("  009後 joint reliability (非悪化が必須ゲート):")
    for bt, jr in joint.items():
        print(f"    {bt:<9} NLL raw={jr.nll_p:.4f} p'={jr.nll_pp:.4f} "
              f"not_degraded={jr.not_degraded} (n={jr.n_races})")
    adopt = rep.improved and all(jr.not_degraded for jr in joint.values())
    print(f"  ADOPT(主NLL改善 かつ joint 非悪化)={adopt}")
    print("  ※Kelly リスク非悪化は kelly-calibration-compare で別途確認")
    return 0


def _cmd_fl_evaluate(session: Session, args) -> int:
    from .fl_bias import fit_fl_calibrator, load_samples
    from .market_calibration import evaluate_q_vs_qprime
    if not (args.train_to < args.eval_from):  # strictly-before: no leak / no overlap
        raise SystemExit("eval window must start strictly after train window (walk-forward)")
    train = load_samples(session, date_from=args.train_from, date_to=args.train_to)
    cal = fit_fl_calibrator([(wo, w) for _, _, wo, w in train], method=args.method,
                            train_window=(args.train_from, args.train_to))
    ev = load_samples(session, date_from=args.eval_from, date_to=args.eval_to)
    rep = evaluate_q_vs_qprime([(wo, w) for _, _, wo, w in ev], cal)
    print(f"fl-evaluate train={args.train_from}..{args.train_to} "
          f"eval={args.eval_from}..{args.eval_to}"
          f"  [PSEUDO 疑似 / 採否=勝率校正]  gamma={cal.params['gamma']:.4f}")
    print(f"  {'metric':<8} {'raw q':>10} {'corr q′':>10}")
    print(f"  {'NLL':<8} {rep.nll_q:>10.5f} {rep.nll_qp:>10.5f}")
    print(f"  {'Brier':<8} {rep.brier_q:>10.5f} {rep.brier_qp:>10.5f}")
    print(f"  {'ECE':<8} {rep.ece_q:>10.5f} {rep.ece_qp:>10.5f}")
    print(f"  races={rep.n_races}  improved(q′ beats q on NLL)={rep.improved}")
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

    eo = sub.add_parser("estimate-odds", help="estimated market odds (pseudo) from win odds")
    eo.add_argument("--race-id", required=True)
    eo.add_argument("--top", type=int, default=10)
    eo.add_argument("--database-url", default=None)

    vo = sub.add_parser("validate-odds", help="win-odds recovery + q calibration (pseudo)")
    vo.add_argument("--from", dest="from_", type=_parse_date, required=True)
    vo.add_argument("--to", type=_parse_date, required=True)
    vo.add_argument("--database-url", default=None)

    ff = sub.add_parser("fl-fit", help="fit favorite-longshot bias calibrator q→q' (walk-forward)")
    ff.add_argument("--train-from", type=_parse_date, required=True)
    ff.add_argument("--train-to", type=_parse_date, required=True)
    ff.add_argument("--method", default="power")
    ff.add_argument("--database-url", default=None)

    ce = sub.add_parser("calibrate-eval", help="model p→p' calibration eval (walk-forward, 017)")
    ce.add_argument("--from", dest="from_", type=_parse_date, required=True)
    ce.add_argument("--to", type=_parse_date, required=True)
    ce.add_argument("--method", default="power")
    ce.add_argument("--train-frac", dest="train_frac", type=float, default=0.5)
    ce.add_argument("--min-races", dest="min_races", type=int, default=50)
    ce.add_argument("--min-wins", dest="min_wins", type=int, default=30)
    ce.add_argument("--model-version", default=None)
    ce.add_argument("--database-url", default=None)

    fe = sub.add_parser("fl-evaluate", help="q vs q' win-rate calibration (adoption gate, pseudo)")
    fe.add_argument("--train-from", type=_parse_date, required=True)
    fe.add_argument("--train-to", type=_parse_date, required=True)
    fe.add_argument("--eval-from", type=_parse_date, required=True)
    fe.add_argument("--eval-to", type=_parse_date, required=True)
    fe.add_argument("--method", default="power")
    fe.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "show":
            if (args.prediction_run is None) == (args.race_id is None):
                parser.error("exactly one of --prediction-run or --race-id is required")
            return _cmd_show(session, args)
        if args.command == "calibrate":
            return _cmd_calibrate(session, args)
        if args.command == "estimate-odds":
            return _cmd_estimate_odds(session, args)
        if args.command == "validate-odds":
            return _cmd_validate_odds(session, args)
        if args.command == "calibrate-eval":
            return _cmd_calibrate_eval(session, args)
        if args.command == "fl-fit":
            return _cmd_fl_fit(session, args)
        if args.command == "fl-evaluate":
            return _cmd_fl_evaluate(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
