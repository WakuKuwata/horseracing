"""Operator CLI: recommend (race/run) and backtest (period) — quickstart.md."""

from __future__ import annotations

import argparse
import datetime

from horseracing_db.enums import AdoptionStatus, BetType, EntryStatus
from horseracing_db.models import (
    ModelVersion,
    PredictionRun,
    RaceHorse,
    Recommendation,
)
from horseracing_db.session import create_db_engine
from sqlalchemy import case, func, select
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


def _resolve_active_run(session: Session, race_id: str):
    """Feature 043: SAME run the read API shows — active model → computed_at DESC → id DESC.

    Mirrors api.selection.select_prediction_run (cannot import api from betting). Returns the
    prediction_run_id or None (no run for the race). Keeps generation and display on one run.
    """
    active_first = case(
        (ModelVersion.adoption_status == AdoptionStatus.ACTIVE, 0), else_=1
    )
    return session.scalars(
        select(PredictionRun.prediction_run_id)
        .join(ModelVersion, PredictionRun.model_version == ModelVersion.model_version)
        .where(PredictionRun.race_id == race_id)
        .order_by(
            active_first,
            PredictionRun.computed_at.desc(),
            PredictionRun.prediction_run_id.desc(),
        )
    ).first()


def _race_has_win_odds(session: Session, race_id: str) -> bool:
    """True if at least one STARTED horse has a positive win odds (gates recommendations)."""
    row = session.scalars(
        select(RaceHorse.odds)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .where(RaceHorse.odds.is_not(None))
    ).first()
    return row is not None


def _has_group(session: Session, run_id, bet_types) -> bool:
    """Feature 045: does the run already have a recommendation in this bet_type group?"""
    return session.scalars(
        select(Recommendation.recommendation_id)
        .where(Recommendation.prediction_run_id == run_id)
        .where(Recommendation.bet_type.in_(bet_types))
    ).first() is not None


def _fit_product_p_calibrator(session: Session, *, before_date, target_race_id: str):
    """Feature 046/048: walk-forward model-p calibrator for the product path (017 machinery).

    Fits the calibrator on persisted predictions × winners STRICTLY before the target race
    (race_before date+id tie-break). Feature 048: method=two_gamma (asymmetric two-piece power,
    pivot=0.15 pre-registered) — adopted over uniform power on the pre-registered A/B
    (eval NLL 2.2194→2.1954, joint exacta+trifecta not_degraded=True). Insufficient data
    (min_races/min_wins, 017 defaults) → identity fallback — the calibrated path then equals
    the raw path. The sample scan is bounded to the era that actually has prediction_runs
    (cheap; avoids a 2007+ full-table walk).
    """
    from horseracing_db.models import PredictionRun, Race
    from horseracing_probability.model_calibration import (
        fit_p_calibrator,
        load_p_samples,
        split_before,
    )

    first = session.scalar(
        select(func.min(Race.race_date))
        .select_from(PredictionRun)
        .join(Race, Race.race_id == PredictionRun.race_id)
    )
    if first is None:  # no persisted predictions at all -> identity
        return fit_p_calibrator([], base_model_version=None)
    samples = load_p_samples(session, date_from=first, date_to=before_date)
    train = split_before(samples, before_date, target_race_id)
    return fit_p_calibrator(
        [(p, w) for (_rid, _d, p, w, _dh) in train], method="two_gamma"
    )


def _fit_product_stage_discount(session: Session, *, before_date, p_calibrator=None):
    """Feature 049: walk-forward top2/top3 discount for the EXOTIC product path. Per research D4
    (distribution match) the fit sample p is passed through the SAME two_gamma p_calibrator that
    the engine input uses, so λ is fit on the distribution it will be applied to. Under-sampled →
    identity (no-op)."""
    from horseracing_probability.model_calibration import (
        apply_p_calibrator,
        fit_product_stage_discount,
    )

    cal = None
    if p_calibrator is not None:
        cal = lambda pd: apply_p_calibrator(pd, p_calibrator)  # noqa: E731
    return fit_product_stage_discount(session, before_date=before_date, calibrator=cal)


def _generate_product_set(
    session: Session, run_id, *, p_calibrator=None, stage_discount=None
) -> tuple[int, int, list[str]]:
    """Generate the missing bet_type groups for one run (group-wise idempotent, Feature 045).

    win = 007 EV on real win odds + 016 Kelly sizing; exotic = 016 Kelly set (043). A group that
    already exists is skipped (no append-only duplication; existing 043 runs get win topped up).
    Feature 046: the walk-forward p calibrator is applied to BOTH groups (identity when
    insufficient) and recorded in logic_version. Feature 049: the top2/top3 stage discount is
    applied to the EXOTIC group only (win Kelly is unaffected — win prob is untouched).
    Returns (n_win, n_exotic, skipped_group_names).
    """
    cfg = KellyConfig()
    n_win = n_exotic = 0
    skipped: list[str] = []
    if _has_group(session, run_id, (BetType.WIN,)):
        skipped.append("win")
    else:
        n_win = len(generate_recommendations(
            session, prediction_run_id=run_id, cfg=cfg, p_calibrator=p_calibrator))
    if _has_group(session, run_id, BetType.EXOTIC):
        skipped.append("exotic")
    else:
        n_exotic = len(generate_kelly_recommendations(
            session, prediction_run_id=run_id, cfg=cfg, p_calibrator=p_calibrator,
            stage_discount=stage_discount))
    return n_win, n_exotic, skipped


def _cmd_recommend_serve(session: Session, args) -> int:
    """Feature 043/045: product-flow generation for ONE race — win (007+Kelly) + exotic (016),
    active-model run, group-wise idempotent. Prints a machine-parseable SKIPPED/OK line the ops
    runner maps to skipped/succeeded (028-style). Exit non-zero only on genuine failure.
    """
    race_id = args.race_id
    run_id = _resolve_active_run(session, race_id)
    if run_id is None:
        print(f"SKIPPED: no prediction_run for race {race_id} (predict first)")
        return 0
    if _has_group(session, run_id, BetType.ALL):
        # some group exists — top up only the missing groups (045); all present → full skip
        if (_has_group(session, run_id, (BetType.WIN,))
                and _has_group(session, run_id, BetType.EXOTIC)):
            print(f"SKIPPED: recommendations already exist for run {run_id}")
            return 0
    if not _race_has_win_odds(session, race_id):
        print(f"SKIPPED: no win odds for race {race_id} (recommendations need odds)")
        return 0
    # Feature 046: walk-forward p calibrator (strictly before this race; identity when thin)
    from horseracing_db.models import Race
    race = session.get(Race, race_id)
    has_date = race is not None and race.race_date is not None
    pcal = _fit_product_p_calibrator(
        session, before_date=race.race_date, target_race_id=race_id,
    ) if has_date else None
    # Feature 049: top2/top3 discount for exotic P_model — OPT-IN (default OFF). The pre-registered
    # exotic pseudo-ROI MUST gate failed on trio, so the product default stays λ=1.
    sdisc = _fit_product_stage_discount(
        session, before_date=race.race_date, p_calibrator=pcal,
    ) if (has_date and getattr(args, "stage_discount", False)) else None
    n_win, n_exotic, skipped = _generate_product_set(
        session, run_id, p_calibrator=pcal, stage_discount=sdisc,
    )
    note = f" (skipped groups: {','.join(skipped)})" if skipped else ""
    pnote = f" pcal={pcal.logic_version}" if pcal is not None else ""
    snote = ""
    if sdisc is not None:
        from horseracing_eval.stage_discount import logic_version_fragment
        snote = f" {logic_version_fragment(sdisc)}"
    print(f"OK: run={run_id} win={n_win} exotic={n_exotic}{note}{pnote}{snote}")
    return 0


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
        haircut_type=getattr(args, "haircut_type", "none"),
        haircut=getattr(args, "haircut", 0.0),
    )


def _p_calibrator_from_args(args):
    """Build a PCalibrator from --p-gamma (explicit power) or None. (017)"""
    gamma = getattr(args, "p_gamma", None)
    if gamma is None:
        return None
    from horseracing_probability.model_calibration import PCalibrator
    return PCalibrator(
        method="power", params={"gamma": float(gamma)}, train_window=None, n_races=0,
        n_samples=0, prob_range=(0.0, 1.0), select="explicit", base_model_version=None,
        logic_version=f"pcal=power(p^gamma);gamma={float(gamma):.5f};select=explicit",
        sufficient=True,
    )


def _cmd_kelly_recommend(session: Session, args) -> int:
    run_id = args.prediction_run or _resolve_run(session, args.race_id)
    cfg = _cfg_from_args(args)
    ids = generate_kelly_recommendations(
        session, prediction_run_id=run_id, cfg=cfg, threshold=args.threshold,
        top_k=args.top_k, bet_types=_parse_bet_types(args.bet_types), odds_cap=args.odds_cap,
        use_real_odds=args.use_real_odds, p_calibrator=_p_calibrator_from_args(args),
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


def _cmd_recommend_backfill(session: Session, args) -> int:
    """Feature 043 US3: idempotently generate the recommendation set for every race with a
    prediction_run + odds in [from, to]. Per-race exception isolation (one failure doesn't abort);
    prints generated / skipped-by-reason counts.
    """
    from horseracing_db.models import Race
    rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= args.from_)
        .where(Race.race_date <= args.to)
        .order_by(Race.race_date, Race.race_id)
    ).all()
    counts = {"generated": 0, "topped_up": 0, "skip_no_run": 0, "skip_no_odds": 0,
              "skip_exists": 0, "error": 0}
    # Feature 046: fit the p calibrator ONCE per day (samples strictly before that day — the
    # date-level cutoff excludes same-day races, matching the 004 date-level convention).
    pcal_day = None
    pcal = None
    sdisc = None
    for rid, rdate in rows:
        try:
            run_id = _resolve_active_run(session, rid)
            if run_id is None:
                counts["skip_no_run"] += 1
                continue
            has_win = _has_group(session, run_id, (BetType.WIN,))
            has_exotic = _has_group(session, run_id, BetType.EXOTIC)
            if has_win and has_exotic:  # group-wise idempotent (045)
                counts["skip_exists"] += 1
                continue
            if not _race_has_win_odds(session, rid):
                counts["skip_no_odds"] += 1
                continue
            if rdate != pcal_day:
                # cutoff = the day itself with a smaller-than-any race_id → strictly before the day
                pcal = _fit_product_p_calibrator(session, before_date=rdate, target_race_id="")
                # Feature 049 discount OPT-IN (default OFF — exotic trio MUST gate failed)
                sdisc = (_fit_product_stage_discount(session, before_date=rdate, p_calibrator=pcal)
                         if getattr(args, "stage_discount", False) else None)
                pcal_day = rdate
            _generate_product_set(session, run_id, p_calibrator=pcal, stage_discount=sdisc)
            # a partial run (043-era exotic-only) counts as a top-up, a bare run as generated
            counts["topped_up" if (has_win or has_exotic) else "generated"] += 1
        except Exception as exc:  # noqa: BLE001 — one race must not abort the whole backfill
            session.rollback()
            counts["error"] += 1
            print(f"  error {rid}: {type(exc).__name__}: {exc}")
    total = len(rows)
    print(f"recommend-backfill {args.from_}..{args.to}  races={total}")
    print(f"  generated={counts['generated']} topped_up={counts['topped_up']} "
          f"skip_exists={counts['skip_exists']} skip_no_run={counts['skip_no_run']} "
          f"skip_no_odds={counts['skip_no_odds']} error={counts['error']}")
    assert sum(counts.values()) == total, "count reconciliation failed"
    return 0


def _cmd_kelly_calibration_compare(session: Session, args) -> int:
    from horseracing_probability.model_calibration import fit_p_calibrator, load_p_samples

    from .calibration_eval import compare_calibration_modes
    cfg = _cfg_from_args(args)
    p_calibrator = _p_calibrator_from_args(args)
    if p_calibrator is None and args.p_train_from is not None:
        samples = load_p_samples(session, date_from=args.p_train_from, date_to=args.p_train_to)
        p_calibrator = fit_p_calibrator(
            [(p, w) for (_rid, _d, p, w, _dh) in samples], method="power",
            train_window=(args.p_train_from, args.p_train_to),
        )
    report = compare_calibration_modes(
        session, date_from=args.from_, date_to=args.to, cfg=cfg, p_calibrator=p_calibrator,
        threshold=args.threshold, top_k=args.top_k, bet_types=_parse_bet_types(args.bet_types),
        odds_cap=args.odds_cap, model_version=args.model_version,
        ruin_threshold=args.ruin_threshold, bootstrap_blocks=args.bootstrap_blocks, seed=args.seed,
    )
    print(f"kelly-calibration-compare {args.from_}..{args.to}  [校正は p 系統のみ(p≠q)]")
    print(f"  {'mode':<14}{'term_bank':>10}{'logGrow':>9}{'maxDD':>8}{'ruin':>6}"
          f"{'var':>9}{'bets':>6}  risk_ok over_cons")
    for r in report.results:
        s = r.segment
        print(f"  {r.mode:<14}{s.terminal_bankroll:>10.2f}{s.log_growth_rate:>9.4f}"
              f"{s.max_drawdown:>8.2f}{s.ruin_probability:>6.2f}{s.variance:>9.5f}{s.n_bets:>6}"
              f"   {str(r.risk_not_worse):<6} {r.over_conservative}")
    print(f"  {report.verdict}")
    print("  success = 校正で Kelly リスク非悪化(最大DD/破産非悪化)かつ成長維持。ROI>1 単独不可")
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
        # Feature 017: edge haircut + explicit model-p calibrator (power gamma).
        sp.add_argument("--haircut-type", dest="haircut_type",
                        choices=["none", "relative", "absolute"], default="none")
        sp.add_argument("--haircut", type=float, default=0.0)
        sp.add_argument("--p-gamma", dest="p_gamma", type=float, default=None,
                        help="model-p power calibrator exponent (017); omit for raw p")

    kr = sub.add_parser("kelly-recommend", help="generate Kelly-sized exotic recommendations")
    kr.add_argument("--prediction-run", default=None)
    kr.add_argument("--race-id", default=None)
    _add_kelly_knobs(kr)
    kr.add_argument("--no-real-odds", dest="use_real_odds", action="store_false")
    kr.set_defaults(use_real_odds=True)
    kr.add_argument("--database-url", default=None)

    # Feature 043: product-flow single-race generation (active-model run, idempotent, Kelly set).
    rs = sub.add_parser("recommend-serve",
                        help="generate the product recommendation set for ONE race (043)")
    rs.add_argument("--race-id", required=True)
    rs.add_argument("--stage-discount", dest="stage_discount", action="store_true",
                    help="049: apply top2/top3 Benter discount to exotic P_model (OPT-IN; default "
                         "OFF — exotic trio pseudo-ROI MUST gate failed)")
    rs.add_argument("--database-url", default=None)

    rb = sub.add_parser("recommend-backfill",
                        help="idempotently generate recommendation sets over a date range (043)")
    rb.add_argument("--from", dest="from_", type=_parse_date, required=True)
    rb.add_argument("--to", type=_parse_date, required=True)
    rb.add_argument("--stage-discount", dest="stage_discount", action="store_true",
                    help="049: apply top2/top3 discount to exotic P_model (OPT-IN; default OFF)")
    rb.add_argument("--database-url", default=None)

    kc = sub.add_parser("kelly-calibration-compare",
                        help="raw / cal / cal+haircut Kelly risk comparison (017)")
    kc.add_argument("--from", dest="from_", type=_parse_date, required=True)
    kc.add_argument("--to", type=_parse_date, required=True)
    _add_kelly_knobs(kc)
    kc.add_argument("--p-train-from", dest="p_train_from", type=_parse_date, default=None,
                    help="fit model-p calibrator on [p-train-from, p-train-to] (walk-forward)")
    kc.add_argument("--p-train-to", dest="p_train_to", type=_parse_date, default=None)
    kc.add_argument("--ruin-threshold", dest="ruin_threshold", type=float, default=0.0)
    kc.add_argument("--bootstrap-blocks", dest="bootstrap_blocks", type=int, default=200)
    kc.add_argument("--seed", type=int, default=20260626)
    kc.add_argument("--model-version", default=None)
    kc.add_argument("--database-url", default=None)

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

    # Feature 049: exotic pseudo-ROI non-degradation gate (λ=1 vs walk-forward λ̂).
    sc = sub.add_parser("stage-discount-backtest-compare",
                        help="049: place/wide/trio pseudo-ROI, λ=1 vs walk-forward λ̂ (MUST gate)")
    sc.add_argument("--from", dest="from_", type=_parse_date, required=True)
    sc.add_argument("--to", type=_parse_date, required=True)
    sc.add_argument("--min-races", type=int, default=300)
    sc.add_argument("--model-version", default=None)
    sc.add_argument("--database-url", default=None)

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
        if args.command == "recommend-serve":
            return _cmd_recommend_serve(session, args)
        if args.command == "recommend-backfill":
            return _cmd_recommend_backfill(session, args)
        if args.command == "kelly-backtest":
            return _cmd_kelly_backtest(session, args)
        if args.command == "kelly-calibration-compare":
            return _cmd_kelly_calibration_compare(session, args)
        if args.command == "exotic-backtest":
            return _cmd_exotic_backtest(session, args)
        if args.command == "exotic-divergence":
            return _cmd_exotic_divergence(session, args)
        if args.command == "stage-discount-backtest-compare":
            return _cmd_stage_discount_compare(session, args)
    return 1


def _cmd_stage_discount_compare(session, args) -> int:
    from .stage_discount_compare import compare_stage_discount_roi

    r = compare_stage_discount_roi(
        session, date_from=args.from_, date_to=args.to,
        min_races=args.min_races, model_version=args.model_version,
    )
    print(r.summary())
    return 0 if r.must_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
