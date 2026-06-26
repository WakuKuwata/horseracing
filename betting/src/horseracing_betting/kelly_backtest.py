"""Bankroll backtest: Kelly vs flat, walk-forward (Feature 016, contracts/kelly_backtest.md).

Walk-forward (race_id time order) sequential bankroll update. Selection/sizing use the ESTIMATED
O_est (no lookahead — past-race exotic_odds is overwritten to the final dividend, so it cannot be a
selection input); the real dividend is used only for PAYOUT/scoring. Each strategy×segment runs an
independent bankroll trajectory from the same starting bankroll. "real" / "double_pseudo" segments
are scored separately (never combined): a bet pays the real dividend when present (real segment)
else the estimated O_est (double-pseudo segment).

Ruin probability: the actual path is one realization, so it is estimated by a SEEDED block
bootstrap over the per-race return sequence (blocks preserve serial order/correlation; a plain
i.i.d. shuffle is forbidden — research.md R6). The seed is recorded in the report so the estimate is
reproducible (analyze F1). success = Kelly beats flat on risk-adjusted growth (higher log AND ruin
within tolerance), NOT merely ROI > 1. Bet selection never reads results (leak boundary).
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass
from math import log, sqrt

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Race, RaceHorse, RaceResult
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.predictor import predict_race
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exotic_ev import candidate_bets, canonical_field
from .exotic_market import load_real_exotic_odds
from .exotic_selection import is_hit
from .exotic_types import ALL_EXOTIC, ExoticRaceOutcome
from .kelly_allocation import allocate_kelly
from .kelly_sizing import single_kelly
from .kelly_types import KellyConfig

SEGMENTS = ("all", "real", "double_pseudo")


@dataclass(frozen=True)
class BankrollSegment:
    strategy: str            # kelly / flat
    segment: str             # all / real / double_pseudo
    terminal_bankroll: float
    log_growth_rate: float   # mean log per-race return
    max_drawdown: float      # max relative peak-to-trough on the bankroll path
    ruin_probability: float  # seeded block-bootstrap estimate
    variance: float          # variance of log per-race returns
    max_losing_streak: int
    n_bets: int
    n_hits: int
    hit_rate: float


@dataclass(frozen=True)
class BankrollBacktestReport:
    date_from: datetime.date
    date_to: datetime.date
    bankroll0: float
    seed: int
    bootstrap_blocks: int
    segments: list[BankrollSegment]
    verdict: str


@dataclass(frozen=True)
class _PlacedBet:
    bet_type: str
    selection: list[int]
    fraction: float       # Kelly effective fraction for this bet
    o_payout: float       # odds used for payout (real dividend if present, else O_est)
    is_real_payout: bool  # True → real segment; False → double_pseudo segment
    hit: bool


def _field_and_outcome(session, model, race_id, feature_rows):
    preds, _ = predict_race(model, race_id, feature_rows)
    if not preds:
        return None, None
    predictions: dict[int, float | None] = {}
    odds: dict[int, float | None] = {}
    scratched: dict[int, str] = {}
    number_to_id: dict[int, str] = {}
    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == race_id)):
        if rh.horse_number is None:
            continue
        n = int(rh.horse_number)
        number_to_id[n] = rh.horse_id
        if rh.entry_status in EntryStatus.NON_STARTERS:
            scratched[n] = rh.entry_status
            continue
        predictions[n] = preds[rh.horse_id].win if rh.horse_id in preds else None
        odds[n] = float(rh.odds) if rh.odds is not None else None
    field = canonical_field(race_id, predictions, odds, scratched=scratched,
                            number_to_id=number_to_id)
    id_to_number = {v: k for k, v in number_to_id.items()}
    finish_pos: dict[int, int] = {}
    for res in session.scalars(
        select(RaceResult).where(RaceResult.race_id == race_id)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
    ):
        if res.finish_order is None:
            continue
        n = id_to_number.get(res.horse_id)
        if n is not None:
            finish_pos[n] = int(res.finish_order)
    return field, ExoticRaceOutcome(race_id, finish_pos, field.field_size)


def _placed_bets_for_race(field, outcome, real_odds, *, cfg, threshold, top_k, bet_types,
                          payout_rates, odds_cap):
    """Kelly-eligible bets for one race (sized on estimated O_est; payout source recorded)."""
    cands = candidate_bets(field, bet_types=bet_types, payout_rates=payout_rates, odds_cap=odds_cap)
    placed: list[_PlacedBet] = []
    for bt, bets in cands.items():
        from .exotic_ev import _k_for
        k = _k_for(top_k, bt)
        if k <= 0:
            continue
        ranked = [b for b in bets if b.ev >= threshold]
        ranked.sort(key=lambda b: -b.ev)
        ranked = ranked[:k]
        group = []
        for b in ranked:
            sized = single_kelly(b.p_model, b.o_est, is_estimated=True, cfg=cfg)
            if sized is None:
                continue
            _edge, raw, _eff = sized
            group.append((b, raw))
        if not group:
            continue
        fractions = allocate_kelly(
            [(b.p_model, b.o_est, True, raw) for (b, raw) in group], cfg=cfg
        )
        for (b, _raw), frac in zip(group, fractions, strict=True):
            if frac <= 0.0:
                continue
            hit = is_hit(b.bet_type, b.selection, outcome.finish_pos, field_size=outcome.field_size)
            if hit is None:  # dead-heat ambiguous → audit skip
                continue
            real = real_odds.get((b.bet_type, tuple(b.selection)))
            o_payout, is_real = (real, True) if real is not None else (b.o_est, False)
            placed.append(_PlacedBet(b.bet_type, list(b.selection), frac, float(o_payout),
                                     is_real, bool(hit)))
    return placed


def _segment_returns(races_bets, *, strategy, segment, bankroll0, flat_stake, ruin_threshold):
    """Sequential bankroll path for one strategy×segment → (returns, path, n_bets, n_hits)."""
    bankroll = bankroll0
    path = [bankroll0]
    returns: list[float] = []
    n_bets = n_hits = 0
    ruined = False
    for placed in races_bets:
        bets = [
            b for b in placed
            if segment == "all" or (segment == "real") == b.is_real_payout
        ]
        if not bets or ruined:
            continue
        race_profit = 0.0
        for b in bets:
            stake = (b.fraction * bankroll) if strategy == "kelly" else flat_stake
            race_profit += stake * (b.o_payout - 1.0) if b.hit else -stake
            n_bets += 1
            n_hits += 1 if b.hit else 0
        new_bankroll = bankroll + race_profit
        if bankroll > 0:
            returns.append(max(new_bankroll, 1e-9) / bankroll)
        bankroll = new_bankroll
        path.append(bankroll)
        if bankroll <= ruin_threshold:
            ruined = True
    return returns, path, n_bets, n_hits


def _max_drawdown(path: list[float]) -> float:
    peak = path[0]
    mdd = 0.0
    for w in path:
        peak = max(peak, w)
        if peak > 0:
            mdd = max(mdd, (peak - w) / peak)
    return mdd


def _variance(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _max_losing_streak(returns: list[float]) -> int:
    streak = best = 0
    for r in returns:
        if r < 1.0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def _bootstrap_ruin(returns: list[float], *, bankroll0, ruin_threshold, n_samples, seed) -> float:
    """Seeded block bootstrap over returns (blocks preserve serial order) → ruin fraction."""
    n = len(returns)
    if n == 0:
        return 0.0
    rng = random.Random(seed)
    block = max(1, round(sqrt(n)))
    ruined = 0
    for _ in range(n_samples):
        path_len = 0
        bankroll = bankroll0
        hit_ruin = False
        while path_len < n:
            start = rng.randrange(n)
            for j in range(block):
                if path_len >= n:
                    break
                r = returns[(start + j) % n]
                bankroll *= r
                path_len += 1
                if bankroll <= ruin_threshold:
                    hit_ruin = True
                    break
            if hit_ruin:
                break
        ruined += 1 if hit_ruin else 0
    return ruined / n_samples


def run_bankroll_backtest(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    cfg: KellyConfig | None = None,
    threshold: float = 1.0,
    top_k: int | dict[str, int] = 5,
    bet_types=ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
    model_version: str | None = None,
    ruin_threshold: float = 0.0,
    bootstrap_blocks: int = 200,
    seed: int = 20260626,
    flat_stake: float | None = None,
) -> BankrollBacktestReport:
    cfg = cfg or KellyConfig()
    # flat baseline bets a fixed amount per bet (011/012 semantics): cap_bet × bankroll0 by default.
    flat_stake = flat_stake if flat_stake is not None else cfg.cap_bet * cfg.bankroll

    model = load_serving_model(session, model_version)
    feature_rows = build_feature_matrix(session, end_date=date_to)
    present = set(feature_rows["race_id"].unique())
    races = session.execute(
        select(Race.race_id).where(Race.race_date >= date_from).where(Race.race_date <= date_to)
        .order_by(Race.race_id)
    ).all()

    races_bets: list[list[_PlacedBet]] = []
    for (race_id,) in races:
        if race_id not in present:
            continue
        field, outcome = _field_and_outcome(session, model, race_id, feature_rows)
        if field is None or not field.p_norm:
            continue
        real_odds = load_real_exotic_odds(session, race_id)  # final dividend → PAYOUT only
        races_bets.append(_placed_bets_for_race(
            field, outcome, real_odds, cfg=cfg, threshold=threshold, top_k=top_k,
            bet_types=bet_types, payout_rates=payout_rates, odds_cap=odds_cap,
        ))

    segments: list[BankrollSegment] = []
    growth: dict[str, float] = {}
    ruin: dict[str, float] = {}
    for strategy in ("kelly", "flat"):
        for segment in SEGMENTS:
            returns, path, n_bets, n_hits = _segment_returns(
                races_bets, strategy=strategy, segment=segment, bankroll0=cfg.bankroll,
                flat_stake=flat_stake, ruin_threshold=ruin_threshold,
            )
            logs = [log(r) for r in returns if r > 0]
            lg = sum(logs) / len(logs) if logs else 0.0
            rp = _bootstrap_ruin(returns, bankroll0=cfg.bankroll, ruin_threshold=ruin_threshold,
                                 n_samples=bootstrap_blocks, seed=seed)
            segments.append(BankrollSegment(
                strategy=strategy, segment=segment, terminal_bankroll=path[-1],
                log_growth_rate=lg, max_drawdown=_max_drawdown(path), ruin_probability=rp,
                variance=_variance(logs), max_losing_streak=_max_losing_streak(returns),
                n_bets=n_bets, n_hits=n_hits, hit_rate=(n_hits / n_bets if n_bets else 0.0),
            ))
            if segment == "all":
                growth[strategy] = lg
                ruin[strategy] = rp

    # success = Kelly beats flat on risk-adjusted growth (higher log-growth AND ruin not worse).
    success = growth.get("kelly", 0.0) > growth.get("flat", 0.0) and \
        ruin.get("kelly", 1.0) <= ruin.get("flat", 1.0) + 1e-9
    verdict = "SUCCESS(Kelly>flat risk-adjusted)" if success else "NOT-ADOPTED(flat 超えなし)"

    return BankrollBacktestReport(
        date_from=date_from, date_to=date_to, bankroll0=cfg.bankroll, seed=seed,
        bootstrap_blocks=bootstrap_blocks, segments=segments, verdict=verdict,
    )
