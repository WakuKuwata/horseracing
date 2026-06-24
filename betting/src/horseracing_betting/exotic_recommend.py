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
from .exotic_ev import canonical_field, exotic_ev_bets
from .exotic_types import ALL_EXOTIC

DEFAULT_THRESHOLD = 1.0
DEFAULT_TOP_K = 5
DEFAULT_STAKE = 100.0
DEFAULT_ODDS_CAP = 10000.0


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
        f"exotic_ev=P_model(009;p)*O_est(010;q);thr={threshold};topk={top_k};stake={stake};"
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
    bets = exotic_ev_bets(
        field, threshold=threshold, top_k=top_k, bet_types=bet_types,
        payout_rates=rates, odds_cap=odds_cap,
    )

    ids: list[uuid.UUID] = []
    for b in bets:
        rec = Recommendation(
            prediction_run_id=prediction_run_id,
            race_id=race_id,
            bet_type=b.bet_type,
            selection=list(b.selection),         # JSONB-safe array (no frozenset/tuple)
            market_odds_used=None,                # no real exotic odds
            estimated_market_odds_used=Decimal(str(b.o_est)),
            is_estimated_odds=True,               # DOUBLE-pseudo (est. odds + PL extrapolation)
            pseudo_odds=Decimal(str(b.pseudo_odds)),   # 1 / P_model
            pseudo_roi=Decimal(str(b.pseudo_roi)),     # EV − 1
            logic_version=lv,
        )
        session.add(rec)
        session.flush()
        ids.append(rec.recommendation_id)
    session.commit()
    return ids
