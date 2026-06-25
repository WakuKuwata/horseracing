"""Period exotic pseudo-ROI backtest (contracts/exotic_backtest.md).

Deterministic prediction source: the adopted serving model (``model_version``, default active) is
loaded once and win probs are computed IN-MEMORY per race (no prediction_runs persisted) — the same
deterministic rule the single-win backtest uses. Each race builds ONE canonical field; the EV
strategy and both baselines (lowest_oest / uniform) are scored on it under identical stake/K. Bet
selection never reads results (leak boundary); results enter only at scoring. ALL reports are
DOUBLE-pseudo (estimated odds + PL extrapolation).
"""

from __future__ import annotations

import datetime

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Race, RaceHorse, RaceResult
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.predictor import predict_race
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exotic_ev import candidate_bets, canonical_field, exotic_ev_bets
from .exotic_market import load_real_exotic_odds
from .exotic_roi import aggregate_roi, score_exotic
from .exotic_strategies import lowest_oest_baseline, uniform_baseline
from .exotic_types import (
    ALL_EXOTIC,
    DEFAULT_SEED,
    CanonicalField,
    ExoticRaceOutcome,
    ExoticRoiReport,
)


def _field_and_outcome(session: Session, model, race_id: str, feature_rows):
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
        select(RaceResult)
        .where(RaceResult.race_id == race_id)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
    ):
        if res.finish_order is None:
            continue
        n = id_to_number.get(res.horse_id)
        if n is not None:
            finish_pos[n] = int(res.finish_order)
    outcome = ExoticRaceOutcome(race_id, finish_pos, field.field_size)
    return field, outcome


def _bets_for(strategy: str, field: CanonicalField, *, threshold, top_k, bet_types, seed,
              payout_rates, odds_cap):
    if strategy == "ev":
        return exotic_ev_bets(field, threshold=threshold, top_k=top_k, bet_types=bet_types,
                              payout_rates=payout_rates, odds_cap=odds_cap)
    if strategy == "lowest_oest":
        return lowest_oest_baseline(field, top_k=top_k, bet_types=bet_types,
                                    payout_rates=payout_rates, odds_cap=odds_cap)
    if strategy == "uniform":
        return uniform_baseline(field, top_k=top_k, bet_types=bet_types, seed=seed,
                                payout_rates=payout_rates, odds_cap=odds_cap)
    raise ValueError(f"unknown strategy: {strategy}")


def run_exotic_backtest(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    threshold: float = 1.0,
    top_k: int | dict[str, int] = 5,
    stake: float = 100.0,
    bet_types=ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
    seed: int = DEFAULT_SEED,
    model_version: str | None = None,
    strategies: tuple[str, ...] = ("ev", "lowest_oest", "uniform"),
) -> dict[str, dict[str, ExoticRoiReport]]:
    """Returns {strategy: {bet_type|__total__: ExoticRoiReport}} — all DOUBLE-pseudo."""
    model = load_serving_model(session, model_version)
    feature_rows = build_feature_matrix(session, end_date=date_to)
    present = set(feature_rows["race_id"].unique())
    races = session.execute(
        select(Race.race_id)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_id)
    ).all()

    acc: dict[str, list] = {s: [] for s in strategies}
    opp: dict[str, dict[str, int]] = {s: {} for s in strategies}
    skip: dict[str, dict[str, int]] = {s: {} for s in strategies}

    for (race_id,) in races:
        if race_id not in present:
            continue
        field, outcome = _field_and_outcome(session, model, race_id, feature_rows)
        if field is None or not field.p_norm:
            continue
        # real final dividends — used for PAYOUT/scoring ONLY, never as a selection input (no
        # lookahead: past-race exotic_odds has been overwritten to the final dividend).
        real_odds = load_real_exotic_odds(session, race_id)
        avail = candidate_bets(field, bet_types=bet_types, payout_rates=payout_rates,
                               odds_cap=odds_cap)  # bet types with >=1 candidate (opportunity)
        for s in strategies:
            bets = _bets_for(s, field, threshold=threshold, top_k=top_k, bet_types=bet_types,
                             seed=seed, payout_rates=payout_rates, odds_cap=odds_cap)
            placed_types = {b.bet_type for b in bets}
            for bt in avail:
                opp[s][bt] = opp[s].get(bt, 0) + 1
                if bt not in placed_types:  # opportunity existed but strategy bet nothing
                    skip[s][bt] = skip[s].get(bt, 0) + 1
            scored, _skipped = score_exotic(bets, outcome, stake=stake, real_odds=real_odds)
            acc[s].extend(scored)

    return {
        s: aggregate_roi(acc[s], strategy=s, opportunities=opp[s], skipped=skip[s])
        for s in strategies
    }
