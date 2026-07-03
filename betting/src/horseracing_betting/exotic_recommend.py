"""Generate exotic EV recommendations from a prediction run (contracts/exotic_recommend.md).

EV = P_model(009 on model win prob p) × O_est(010 on market win odds q), computed on ONE canonical
field (valid p AND valid odds). Persists EV≥threshold top-K to ``recommendations`` append-only with
DOUBLE-pseudo disclosure: market_odds_used=null, estimated_market_odds_used=O_est,
is_estimated_odds=true, pseudo_odds=1/P_model, pseudo_roi=EV−1. Selection never reads race results.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from horseracing_db.enums import EntryStatus
from horseracing_db.models import PredictionRun, RaceHorse, RacePrediction, Recommendation
from horseracing_probability.market_odds import DEFAULT_PAYOUT_RATES
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import BETTING_LOGIC_VERSION
from .exotic_ev import _EPS, _k_for, candidate_bets, canonical_field
from .exotic_market import load_real_exotic_odds
from .exotic_selection import selection_key
from .exotic_types import ALL_EXOTIC, ExoticBet

DEFAULT_THRESHOLD = 1.0
DEFAULT_TOP_K = 5
DEFAULT_STAKE = 100.0
DEFAULT_ODDS_CAP = 10000.0


def _blended_bets(field, real_odds, *, threshold, top_k, bet_types, payout_rates, odds_cap,
                  calibrator=None, stage_discount=None):
    """EV candidates with real odds preferred per selection, else estimated O_est (011).

    Returns (bet, odds_used, is_estimated, ev) tuples, EV≥threshold, top-K by (−EV, selection_key).
    EV uses the chosen odds so real-priced bets are ranked on their real EV (row-level distinction).
    ``calibrator`` (013, opt-in) FL-corrects the estimated O_est used for the fallback.
    ``stage_discount`` (049, opt-in) applies the top2/top3 Benter discount to P_model.
    """
    cands = candidate_bets(field, bet_types=bet_types, payout_rates=payout_rates, odds_cap=odds_cap,
                           calibrator=calibrator, stage_discount=stage_discount)
    out: list[tuple[ExoticBet, float, bool, float]] = []
    for bt, bets in cands.items():
        k = _k_for(top_k, bt)
        if k <= 0:
            continue
        scored: list[tuple[ExoticBet, float, bool, float]] = []
        for b in bets:
            real = real_odds.get((b.bet_type, tuple(b.selection)))
            odds_used, is_est = (b.o_est, True) if real is None else (real, False)
            ev = b.p_model * odds_used
            if ev >= threshold - _EPS:
                scored.append((b, odds_used, is_est, ev))
        scored.sort(key=lambda x: (-x[3], selection_key(x[0].bet_type, x[0].selection)))
        out.extend(scored[:k])
    return out


def default_exotic_logic_version(
    *,
    threshold: float,
    top_k: int | dict[str, int],
    stake: float,
    payout_rates: dict[str, float],
    odds_cap: float,
) -> str:
    rates = ",".join(f"{k}={payout_rates[k]}" for k in sorted(payout_rates))
    return (
        f"exotic_ev=P_model(009;p)*odds[real_exotic>est_O_est(010;q)];"
        f"thr={threshold};topk={top_k};stake={stake};"
        f"takeout[{rates}];qsrc=market_win_odds;cap={odds_cap};"
        f"pop=canonical(valid_p&valid_odds;renorm);v={BETTING_LOGIC_VERSION}"
    )


def _load_field_inputs(session: Session, prediction_run_id, race_id: str):
    """Build horse_number-keyed predictions/odds/scratched/number_to_id (no results read)."""
    probs = dict(
        session.execute(
            select(RacePrediction.horse_id, RacePrediction.win_prob).where(
                RacePrediction.prediction_run_id == prediction_run_id
            )
        ).all()
    )
    predictions: dict[int, float | None] = {}
    odds: dict[int, float | None] = {}
    scratched: dict[int, str] = {}
    number_to_id: dict[int, str] = {}
    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == race_id)):
        if rh.horse_number is None:
            continue  # cannot form a numbered selection
        n = int(rh.horse_number)
        number_to_id[n] = rh.horse_id
        if rh.entry_status in EntryStatus.NON_STARTERS:
            scratched[n] = rh.entry_status
            continue
        wp = probs.get(rh.horse_id)
        predictions[n] = float(wp) if wp is not None else None
        odds[n] = float(rh.odds) if rh.odds is not None else None
    return predictions, odds, scratched, number_to_id


def generate_exotic_recommendations(
    session: Session,
    *,
    race_id: str | None = None,
    prediction_run_id,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int | dict[str, int] = DEFAULT_TOP_K,
    stake: float = DEFAULT_STAKE,
    bet_types=ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = DEFAULT_ODDS_CAP,
    use_real_odds: bool = True,
    calibrator=None,
    logic_version: str | None = None,
) -> list[uuid.UUID]:
    run = session.get(PredictionRun, prediction_run_id)
    if run is None:
        raise ValueError(f"prediction_run {prediction_run_id} not found")
    race_id = race_id or run.race_id
    rates = {**DEFAULT_PAYOUT_RATES, **(payout_rates or {})}
    lv = logic_version or default_exotic_logic_version(
        threshold=threshold, top_k=top_k, stake=stake, payout_rates=rates, odds_cap=odds_cap
    )

    predictions, odds, scratched, number_to_id = _load_field_inputs(
        session, prediction_run_id, race_id
    )
    field = canonical_field(
        race_id, predictions, odds, scratched=scratched, number_to_id=number_to_id
    )
    # real exotic odds preferred per selection; estimated O_est (011) as row-level fallback.
    real_odds = load_real_exotic_odds(session, race_id) if use_real_odds else {}
    blended = _blended_bets(
        field, real_odds, threshold=threshold, top_k=top_k, bet_types=bet_types,
        payout_rates=rates, odds_cap=odds_cap, calibrator=calibrator,
    )

    ids: list[uuid.UUID] = []
    for b, odds_used, is_est, ev in blended:
        rec = Recommendation(
            prediction_run_id=prediction_run_id,
            race_id=race_id,
            bet_type=b.bet_type,
            selection=list(b.selection),               # JSONB-safe array (no frozenset/tuple)
            market_odds_used=None if is_est else Decimal(str(odds_used)),   # real exotic odds
            estimated_market_odds_used=Decimal(str(odds_used)) if is_est else None,
            is_estimated_odds=is_est,                  # est=true → DOUBLE-pseudo; false → real ROI
            pseudo_odds=Decimal(str(b.pseudo_odds)),   # 1 / P_model
            pseudo_roi=Decimal(str(ev - 1.0)),         # EV − 1 (EV on the chosen odds)
            logic_version=lv,
        )
        session.add(rec)
        session.flush()
        ids.append(rec.recommendation_id)
    session.commit()
    return ids
