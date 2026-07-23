"""Pure pre-registered adoption gate for real-dividend exotic betting edges."""

from __future__ import annotations

from dataclasses import dataclass
from math import inf

import numpy as np
from horseracing_db.models import Race
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exotic_backtest import ALL_EXOTIC, DEFAULT_SEED, _bets_for, _field_and_outcome
from .exotic_market import load_real_exotic_odds
from .exotic_roi import score_exotic


@dataclass(frozen=True)
class ExoticGateVerdict:
    """Per-bet-type result from the exotic edge adoption gate."""

    bet_type: str
    n_bets: int
    n_days: int
    verdict: str
    point_diff: float
    ci_low: float | None
    ci_high: float | None
    p_value: float | None
    p_adjusted: float | None
    alpha: float
    note: str


def _point_diff(diffs_by_day: dict[str, list[float]]) -> float:
    day_arrays = [
        np.asarray(diffs_by_day[day], dtype=float) for day in sorted(diffs_by_day)
    ]
    all_diffs = np.concatenate(day_arrays) if day_arrays else np.asarray([], dtype=float)
    return float(all_diffs.mean()) if all_diffs.size else float("nan")


def _one_sided_bootstrap_p_value(
    diffs_by_day: dict[str, list[float]],
    *,
    b: int,
    seed: int,
) -> float:
    """Return P(bootstrap mean <= 0) using the registered race-day resampling."""
    days = sorted(diffs_by_day)
    day_arrays = [np.asarray(diffs_by_day[day], dtype=float) for day in days]
    n_days = len(days)
    rng = np.random.default_rng(seed)
    bootstrap_means = np.empty(b, dtype=float)

    for index in range(b):
        picked_days = rng.integers(0, n_days, size=n_days)
        sample = np.concatenate([day_arrays[day_index] for day_index in picked_days])
        bootstrap_means[index] = sample.mean()

    return float(np.mean(bootstrap_means <= 0.0))


def _holm_adjusted_p_values(p_values: dict[str, float]) -> dict[str, float]:
    ordered_bet_types = sorted(p_values, key=lambda bet_type: (p_values[bet_type], bet_type))
    m = len(ordered_bet_types)
    adjusted: dict[str, float] = {}
    running_max = 0.0

    for rank, bet_type in enumerate(ordered_bet_types, start=1):
        multiplier = m - rank + 1
        candidate = min(1.0, p_values[bet_type] * multiplier)
        running_max = max(running_max, candidate)
        adjusted[bet_type] = running_max

    return adjusted


def evaluate_exotic_gate(
    diffs_by_bet_type: dict[str, dict[str, list[float]]],
    n_min: dict[str, int],
    *,
    b: int = 2000,
    seed: int = 20260723,
    alpha: float = 0.05,
) -> dict[str, ExoticGateVerdict]:
    """Evaluate the frozen exotic edge gate independently for each supplied bet type."""
    verdicts: dict[str, ExoticGateVerdict] = {}
    deciding: dict[str, tuple[int, int, float, float, float, float]] = {}

    for bet_type, diffs_by_day in diffs_by_bet_type.items():
        n_bets = sum(len(diffs) for diffs in diffs_by_day.values())
        n_days = len(diffs_by_day)
        point_diff = _point_diff(diffs_by_day)
        required_bets = n_min.get(bet_type, inf)
        insufficient_reasons: list[str] = []

        if n_bets == 0:
            insufficient_reasons.append("no scored model bets")
        elif n_bets < required_bets:
            if required_bets == inf:
                insufficient_reasons.append("n_min is not configured")
            else:
                insufficient_reasons.append(
                    f"n_bets={n_bets} is below n_min={required_bets}"
                )
        if n_days < 2:
            insufficient_reasons.append(f"n_days={n_days} is below 2")

        if insufficient_reasons:
            verdicts[bet_type] = ExoticGateVerdict(
                bet_type=bet_type,
                n_bets=n_bets,
                n_days=n_days,
                verdict="NO_DECISION",
                point_diff=point_diff,
                ci_low=None,
                ci_high=None,
                p_value=None,
                p_adjusted=None,
                alpha=alpha,
                note="; ".join(insufficient_reasons),
            )
            continue

        bootstrap_ci = race_day_cluster_bootstrap_ci_v1(
            diffs_by_day,
            b=b,
            seed=seed,
            alpha=alpha,
        )
        p_value = _one_sided_bootstrap_p_value(
            diffs_by_day,
            b=b,
            seed=seed,
        )
        deciding[bet_type] = (
            n_bets,
            bootstrap_ci.n_days,
            bootstrap_ci.point,
            bootstrap_ci.ci_low,
            bootstrap_ci.ci_high,
            p_value,
        )

    adjusted_p_values = _holm_adjusted_p_values(
        {bet_type: values[-1] for bet_type, values in deciding.items()}
    )
    for bet_type, values in deciding.items():
        n_bets, n_days, point_diff, ci_low, ci_high, p_value = values
        p_adjusted = adjusted_p_values[bet_type]
        adopt = (
            p_adjusted < alpha
            and point_diff > 0.0
            and ci_low is not None
            and ci_low > 0.0
        )
        verdict = "ADOPT_CANDIDATE" if adopt else "REJECT"
        note = (
            "positive edge passes the Holm-adjusted gate"
            if adopt
            else "positive edge does not pass every pre-registered gate condition"
        )
        verdicts[bet_type] = ExoticGateVerdict(
            bet_type=bet_type,
            n_bets=n_bets,
            n_days=n_days,
            verdict=verdict,
            point_diff=point_diff,
            ci_low=ci_low,
            ci_high=ci_high,
            p_value=p_value,
            p_adjusted=p_adjusted,
            alpha=alpha,
            note=note,
        )

    return verdicts


PREREGISTERED_N_MIN = {
    "place": 500,
    "quinella": 500,
    "wide": 500,
    "exacta": 700,
    "trio": 1000,
    "trifecta": 1500,
}


def run_exotic_gate(
    session: Session,
    *,
    date_from,
    date_to,
    n_min: dict[str, int] | None = None,
    baseline: str = "lowest_oest",
    threshold: float = 1.0,
    top_k=5,
    stake: float = 100.0,
    bet_types=ALL_EXOTIC,
    payout_rates=None,
    odds_cap: float = 10000.0,
    seed: int = DEFAULT_SEED,
    model_version: str | None = None,
    b: int = 2000,
    ci_seed: int = 20260723,
    alpha: float = 0.05,
    stage_discount=None,
) -> dict[str, ExoticGateVerdict]:
    """Evaluate model-vs-baseline real-dividend return differences by race day."""
    selected_bet_types = tuple(bet_types)
    model = load_serving_model(session, model_version)
    feature_rows = build_feature_matrix(session, end_date=date_to)
    present = set(feature_rows["race_id"].unique())
    races = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_id)
    ).all()
    diffs_by_bet_type: dict[str, dict[str, list[float]]] = {
        bet_type: {} for bet_type in selected_bet_types
    }

    for race_id, race_date in races:
        if race_id not in present:
            continue
        field, outcome = _field_and_outcome(
            session, model, race_id, feature_rows
        )
        if field is None or not field.p_norm:
            continue
        real_odds = load_real_exotic_odds(session, race_id)
        if not real_odds:
            continue

        model_bets = _bets_for(
            "ev",
            field,
            threshold=threshold,
            top_k=top_k,
            bet_types=selected_bet_types,
            seed=seed,
            payout_rates=payout_rates,
            odds_cap=odds_cap,
            stage_discount=stage_discount,
        )
        baseline_bets = _bets_for(
            baseline,
            field,
            threshold=threshold,
            top_k=top_k,
            bet_types=selected_bet_types,
            seed=seed,
            payout_rates=payout_rates,
            odds_cap=odds_cap,
            stage_discount=stage_discount,
        )
        model_scored, _ = score_exotic(
            model_bets, outcome, stake=stake, real_odds=real_odds
        )
        baseline_scored, _ = score_exotic(
            baseline_bets, outcome, stake=stake, real_odds=real_odds
        )

        model_totals: dict[str, tuple[float, float]] = {}
        baseline_totals: dict[str, tuple[float, float]] = {}
        for scored in model_scored:
            old_stake, old_payout = model_totals.get(scored.bet.bet_type, (0.0, 0.0))
            model_totals[scored.bet.bet_type] = (
                old_stake + scored.stake,
                old_payout + scored.payout,
            )
        for scored in baseline_scored:
            old_stake, old_payout = baseline_totals.get(
                scored.bet.bet_type, (0.0, 0.0)
            )
            baseline_totals[scored.bet.bet_type] = (
                old_stake + scored.stake,
                old_payout + scored.payout,
            )

        for bet_type in selected_bet_types:
            model_stake, model_payout = model_totals.get(bet_type, (0.0, 0.0))
            baseline_stake, baseline_payout = baseline_totals.get(
                bet_type, (0.0, 0.0)
            )
            if model_stake <= 0.0 or baseline_stake <= 0.0:
                continue
            diff = (model_payout / model_stake - 1.0) - (
                baseline_payout / baseline_stake - 1.0
            )
            diffs_by_bet_type[bet_type].setdefault(
                race_date.isoformat(), []
            ).append(diff)

    return evaluate_exotic_gate(
        diffs_by_bet_type,
        n_min or PREREGISTERED_N_MIN,
        b=b,
        seed=ci_seed,
        alpha=alpha,
    )
