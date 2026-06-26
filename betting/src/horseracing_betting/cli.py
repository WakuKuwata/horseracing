"""Operator CLI: recommend (race/run) and backtest (period) — quickstart.md."""

from __future__ import annotations

import argparse
import datetime

from horseracing_db.models import PredictionRun, Recommendation
from horseracing_db.session import create_db_engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from .backtest import run_backtest
from .exotic_backtest import run_exotic_backtest
from .exotic_divergence import exotic_divergence
from .exotic_recommend import (
    DEFAULT_ODDS_CAP,
    DEFAULT_TOP_K,
    generate_exotic_recommendations,
)
from .exotic_roi import TOTAL
from .exotic_types import ALL_EXOTIC
from .kelly_backtest import run_bankroll_backtest
from .kelly_recommend import generate_kelly_recommendations
from .kelly_types import KellyConfig
from .recommend import DEFAULT_STAKE, DEFAULT_THRESHOLD, generate_recommendations

_DOUBLE_PSEUDO = "二重疑似(モデル確率 × 推定市場オッズ / PL 外挿)"


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _parse_bet_types(s: str | None) -> tuple[str, ...]:
    if not s:
        return ALL_EXOTIC
    return tuple(t.strip() for t in s.split(",") if t.strip())


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


def _cmd_exotic_recommend(session: Session, args) -> int:
    run_id = args.prediction_run or _resolve_run(session, args.race_id)
    ids = generate_exotic_recommendations(
        session, prediction_run_id=run_id, threshold=args.threshold, top_k=args.top_k,
        stake=args.stake, bet_types=_parse_bet_types(args.bet_types), odds_cap=args.odds_cap,
        use_real_odds=args.use_real_odds,
    )
    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    recent = [r for r in recs if r.recommendation_id in set(ids)]
    by_type: dict[str, int] = {}
    for r in recent:
        by_type[r.bet_type] = by_type.get(r.bet_type, 0) + 1
    n_real = sum(1 for r in recent if r.is_estimated_odds is False)
    print(f"prediction_run={run_id} exotic recommendations={len(ids)}  ({n_real} real-odds)")
    print(f"  real odds → market_odds_used / 実 ROI; missing → {_DOUBLE_PSEUDO}")
    for bt in ALL_EXOTIC:
        if bt in by_type:
            print(f"  {bt:<9} {by_type[bt]:>3} bets")
    for r in sorted(recent, key=lambda x: float(x.pseudo_roi), reverse=True)[:10]:
        ev = float(r.pseudo_roi) + 1.0
        if r.is_estimated_odds:
            tag = f"O_est={float(r.estimated_market_odds_used):.2f} (推定/二重疑似)"
        else:
            tag = f"O_real={float(r.market_odds_used):.2f} (実)"
        print(f"    {r.bet_type:<9} {r.selection}  EV={ev:.3f}  {tag}")
    return 0


def _cfg_from_args(args) -> KellyConfig:
    return KellyConfig(
        lambda_real=args.lambda_real, lambda_est=args.lambda_est,
        cap_bet=args.cap_bet, cap_total=args.cap_total, o_min=args.o_min,
        bankroll=args.bankroll, allocation=args.allocation,
        enable_estimated=args.enable_estimated,
    )


def _cmd_kelly_recommend(session: Session, args) -> int:
    run_id = args.prediction_run or _resolve_run(session, args.race_id)
    cfg = _cfg_from_args(args)
    ids = generate_kelly_recommendations(
        session, prediction_run_id=run_id, cfg=cfg, threshold=args.threshold,
        top_k=args.top_k, bet_types=_parse_bet_types(args.bet_types), odds_cap=args.odds_cap,
        use_real_odds=args.use_real_odds,
    )
    recs = session.scalars(
        select(Recommendation).where(Recommendation.prediction_run_id == run_id)
    ).all()
    recent = [r for r in recs if r.recommendation_id in set(ids)]
    n_real = sum(1 for r in recent if r.is_estimated_odds is False)
    print(
        f"prediction_run={run_id} kelly recommendations={len(ids)}  ({n_real} real-odds)  "
        f"bankroll={cfg.bankroll} λ_real={cfg.lambda_real} λ_est={cfg.lambda_est} "
        f"alloc={cfg.allocation}"
    )
    print(f"  real odds → 実 stake; estimated → {_DOUBLE_PSEUDO}; stake = fraction × bankroll")
    for r in sorted(recent, key=lambda x: float(x.stake_fraction), reverse=True)[:12]:
        frac = float(r.stake_fraction)
        if r.is_estimated_odds:
            tag = f"O_est={float(r.estimated_market_odds_used):.2f} (推定/二重疑似)"
        else:
            tag = f"O_real={float(r.market_odds_used):.2f} (実)"
        print(
            f"    {r.bet_type:<9} {r.selection}  f={frac:.4f} stake={frac * cfg.bankroll:.2f} "
            f"edge={float(r.pseudo_roi):.3f}  {tag}"
        )
    return 0


def _cmd_kelly_backtest(session: Session, args) -> int:
    cfg = _cfg_from_args(args)
    report = run_bankroll_backtest(
        session, date_from=args.from_, date_to=args.to, cfg=cfg, threshold=args.threshold,
        top_k=args.top_k, bet_types=_parse_bet_types(args.bet_types), odds_cap=args.odds_cap,
        model_version=args.model_version, ruin_threshold=args.ruin_threshold,
        bootstrap_blocks=args.bootstrap_blocks, seed=args.seed,
    )
    print(
        f"kelly-backtest {args.from_}..{args.to}  bankroll0={cfg.bankroll} alloc={cfg.allocation} "
        f"[{_DOUBLE_PSEUDO} 区間は分離集計]"
    )
    hdr = (
        f"{'strategy':<8} {'segment':<13} {'term_bank':>10} {'logGrow':>9} {'maxDD':>8} "
        f"{'ruin':>6} {'var':>9} {'streak':>6} {'bets':>5} {'hit':>6}"
    )
    print(hdr)
    for seg in report.segments:
        print(
            f"{seg.strategy:<8} {seg.segment:<13} {seg.terminal_bankroll:>10.2f} "
            f"{seg.log_growth_rate:>9.4f} {seg.max_drawdown:>8.2f} {seg.ruin_probability:>6.2f} "
            f"{seg.variance:>9.5f} {seg.max_losing_streak:>6} {seg.n_bets:>5} {seg.hit_rate:>6.3f}"
        )
    print(f"  seed={report.seed}  bootstrap_blocks={report.bootstrap_blocks}  {report.verdict}")
    print("  success = flat 比リスク調整後成長で優位(対数成長率↑ かつ 破産/最大DD 許容内)。")
    print("  ROI>1 単独では success としない")
    return 0


def _cmd_exotic_backtest(session: Session, args) -> int:
    reports = run_exotic_backtest(
        session, date_from=args.from_, date_to=args.to, threshold=args.threshold,
        top_k=args.top_k, stake=args.stake, bet_types=_parse_bet_types(args.bet_types),
        odds_cap=args.odds_cap, model_version=args.model_version,
    )
    print(f"exotic-backtest {args.from_}..{args.to}  [{_DOUBLE_PSEUDO} 評価]")
    print(
        f"{'strategy':<12} {'roi':>8} {'hit':>7} {'skip':>7} "
        f"{'bets':>6} {'maxDD':>10} {'streak':>7}"
    )
    for name in ("ev", "lowest_oest", "uniform"):
        r = reports[name][TOTAL]
        print(
            f"{name:<12} {r.roi:>8.3f} {r.hit_rate:>7.3f} {r.skip_rate:>7.3f} "
            f"{r.n_bets:>6} {r.max_drawdown:>10.0f} {r.max_consecutive_losses:>7}"
        )
    print("  success = EV が各 baseline の roi を上回ること(絶対 >1.0 ではない)")
    return 0


def _cmd_exotic_divergence(session: Session, args) -> int:
    reports = exotic_divergence(
        session, date_from=args.from_, date_to=args.to,
        bet_types=_parse_bet_types(args.bet_types), model_version=args.model_version,
    )
    print(f"exotic-divergence {args.from_}..{args.to}  [baseline=推定(010/011) 二重疑似]")
    print(
        f"{'bet_type':<10} {'coverage':>9} {'pairs':>7} "
        f"{'logmed':>8} {'logMAE':>8} {'logP90':>8}"
    )
    for bt in ALL_EXOTIC:
        if bt not in reports:
            continue
        r = reports[bt]
        print(
            f"{bt:<10} {r.coverage_rate:>9.3f} {r.n_pairs:>7} "
            f"{r.log_ratio_median:>8.3f} {r.log_ratio_mae:>8.3f} {r.log_ratio_p90:>8.3f}"
        )
    print("  log = log(実 exotic / 推定 O_est)。coverage 明示(部分カバーを全カバーと誤認しない)")
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

    xr = sub.add_parser("exotic-recommend", help="generate exotic EV recommendations")
    xr.add_argument("--prediction-run", default=None)
    xr.add_argument("--race-id", default=None)
    xr.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    xr.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    xr.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    xr.add_argument("--bet-types", default=None, help="comma list, e.g. trifecta,trio")
    xr.add_argument("--odds-cap", type=float, default=DEFAULT_ODDS_CAP)
    xr.add_argument("--no-real-odds", dest="use_real_odds", action="store_false",
                    help="ignore real exotic_odds; force 011 estimated (double-pseudo)")
    xr.add_argument("--database-url", default=None)

    xb = sub.add_parser("exotic-backtest", help="exotic pseudo-ROI backtest vs baselines (pseudo)")
    xb.add_argument("--from", dest="from_", type=_parse_date, required=True)
    xb.add_argument("--to", type=_parse_date, required=True)
    xb.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    xb.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    xb.add_argument("--stake", type=float, default=DEFAULT_STAKE)
    xb.add_argument("--bet-types", default=None, help="comma list, e.g. trifecta,trio")
    xb.add_argument("--odds-cap", type=float, default=DEFAULT_ODDS_CAP)
    xb.add_argument("--model-version", default=None)
    xb.add_argument("--database-url", default=None)

    def _add_kelly_knobs(sp):
        sp.add_argument("--bankroll", type=float, default=100.0)
        sp.add_argument("--lambda-real", dest="lambda_real", type=float, default=0.25)
        sp.add_argument("--lambda-est", dest="lambda_est", type=float, default=0.10)
        sp.add_argument("--cap-bet", dest="cap_bet", type=float, default=0.05)
        sp.add_argument("--cap-total", dest="cap_total", type=float, default=0.10)
        sp.add_argument("--o-min", dest="o_min", type=float, default=1.5)
        sp.add_argument("--allocation", choices=["exact", "heuristic"], default="exact")
        sp.add_argument("--no-estimated", dest="enable_estimated", action="store_false")
        sp.set_defaults(enable_estimated=True)
        sp.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
        sp.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
        sp.add_argument("--bet-types", default=None, help="comma list, e.g. trifecta,trio")
        sp.add_argument("--odds-cap", type=float, default=DEFAULT_ODDS_CAP)

    kr = sub.add_parser("kelly-recommend", help="generate Kelly-sized exotic recommendations")
    kr.add_argument("--prediction-run", default=None)
    kr.add_argument("--race-id", default=None)
    _add_kelly_knobs(kr)
    kr.add_argument("--no-real-odds", dest="use_real_odds", action="store_false")
    kr.set_defaults(use_real_odds=True)
    kr.add_argument("--database-url", default=None)

    kb = sub.add_parser("kelly-backtest", help="bankroll backtest (Kelly vs flat, double-pseudo)")
    kb.add_argument("--from", dest="from_", type=_parse_date, required=True)
    kb.add_argument("--to", type=_parse_date, required=True)
    _add_kelly_knobs(kb)
    kb.add_argument("--ruin-threshold", dest="ruin_threshold", type=float, default=0.0)
    kb.add_argument("--bootstrap-blocks", dest="bootstrap_blocks", type=int, default=200)
    kb.add_argument("--seed", type=int, default=20260626)
    kb.add_argument("--model-version", default=None)
    kb.add_argument("--database-url", default=None)

    xd = sub.add_parser("exotic-divergence", help="estimated (010/011) vs real exotic odds")
    xd.add_argument("--from", dest="from_", type=_parse_date, required=True)
    xd.add_argument("--to", type=_parse_date, required=True)
    xd.add_argument("--bet-types", default=None, help="comma list, e.g. trifecta,trio")
    xd.add_argument("--model-version", default=None)
    xd.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    engine = create_db_engine(args.database_url)
    with Session(engine) as session:
        if args.command == "recommend":
            if (args.prediction_run is None) == (args.race_id is None):
                parser.error("exactly one of --prediction-run or --race-id is required")
            return _cmd_recommend(session, args)
        if args.command == "backtest":
            return _cmd_backtest(session, args)
        if args.command == "exotic-recommend":
            if (args.prediction_run is None) == (args.race_id is None):
                parser.error("exactly one of --prediction-run or --race-id is required")
            return _cmd_exotic_recommend(session, args)
        if args.command == "kelly-recommend":
            if (args.prediction_run is None) == (args.race_id is None):
                parser.error("exactly one of --prediction-run or --race-id is required")
            return _cmd_kelly_recommend(session, args)
        if args.command == "kelly-backtest":
            return _cmd_kelly_backtest(session, args)
        if args.command == "exotic-backtest":
            return _cmd_exotic_backtest(session, args)
        if args.command == "exotic-divergence":
            return _cmd_exotic_divergence(session, args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
