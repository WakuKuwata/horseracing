"""Generate Kelly-sized exotic recommendations (Feature 016, contracts/kelly_recommend.md).

Reuses 011/012's canonical field and real-preferred-odds blending, then sizes each positive-edge
bet with Kelly: probability is ALWAYS P_model (009 on model p), odds are real exotic (012) when
present else estimated O_est (010, double-pseudo). Per (race, bet_type) the mutually-exclusive bets
are allocated jointly (research.md R2). Persists to recommendations append-only with the new
stake_fraction column; flat 011/012 rows are untouched. Selection never reads results (leak
boundary); the Kelly fraction is never fed back as a model feature.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from horseracing_db.models import PredictionRun, Recommendation
from horseracing_probability.market_odds import DEFAULT_PAYOUT_RATES
from sqlalchemy.orm import Session

from .exotic_ev import canonical_field
from .exotic_market import load_real_exotic_odds
from .exotic_recommend import _blended_bets, _load_field_inputs
from .exotic_types import ALL_EXOTIC
from .kelly_allocation import allocate_kelly
from .kelly_sizing import single_kelly
from .kelly_types import KellyConfig, kelly_logic_version

DEFAULT_THRESHOLD = 1.0   # EV ≥ 1 ⇔ edge ≥ 0
DEFAULT_TOP_K = 5
DEFAULT_ODDS_CAP = 10000.0


def generate_kelly_recommendations(
    session: Session,
    *,
    race_id: str | None = None,
    prediction_run_id,
    cfg: KellyConfig | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int | dict[str, int] = DEFAULT_TOP_K,
    bet_types=ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = DEFAULT_ODDS_CAP,
    use_real_odds: bool = True,
    calibrator=None,
    logic_version: str | None = None,
) -> list[uuid.UUID]:
    cfg = cfg or KellyConfig()
    run = session.get(PredictionRun, prediction_run_id)
    if run is None:
        raise ValueError(f"prediction_run {prediction_run_id} not found")
    race_id = race_id or run.race_id
    rates = {**DEFAULT_PAYOUT_RATES, **(payout_rates or {})}
    lv = logic_version or kelly_logic_version(cfg, odds_cap=odds_cap, threshold=threshold)

    predictions, odds, scratched, number_to_id = _load_field_inputs(
        session, prediction_run_id, race_id
    )
    field = canonical_field(
        race_id, predictions, odds, scratched=scratched, number_to_id=number_to_id
    )
    real_odds = load_real_exotic_odds(session, race_id) if use_real_odds else {}
    blended = _blended_bets(
        field, real_odds, threshold=threshold, top_k=top_k, bet_types=bet_types,
        payout_rates=rates, odds_cap=odds_cap, calibrator=calibrator,
    )

    # Kelly-filter each candidate, then group by bet_type for joint allocation.
    groups: dict[str, list] = {}
    for b, odds_used, is_est, _ev in blended:
        sized = single_kelly(b.p_model, odds_used, is_estimated=is_est, cfg=cfg)
        if sized is None:  # negative edge / below o_min / estimated disabled
            continue
        edge, raw, _eff = sized
        groups.setdefault(b.bet_type, []).append((b, odds_used, is_est, edge, raw))

    ids: list[uuid.UUID] = []
    for _bt, items in groups.items():
        fractions = allocate_kelly(
            [(b.p_model, odds_used, is_est, raw) for (b, odds_used, is_est, _e, raw) in items],
            cfg=cfg,
        )
        for (b, odds_used, is_est, edge, _raw), frac in zip(items, fractions, strict=True):
            if frac <= 0.0:
                continue
            rec = Recommendation(
                prediction_run_id=prediction_run_id,
                race_id=race_id,
                bet_type=b.bet_type,
                selection=list(b.selection),
                market_odds_used=None if is_est else Decimal(str(odds_used)),
                estimated_market_odds_used=Decimal(str(odds_used)) if is_est else None,
                is_estimated_odds=is_est,                # est → double-pseudo (API derives flag)
                pseudo_odds=Decimal(str(b.pseudo_odds)),  # 1 / P_model
                pseudo_roi=Decimal(str(edge)),            # edge = P_model·odds_used − 1
                stake_fraction=Decimal(str(frac)),        # Kelly effective fraction (016)
                logic_version=lv,
            )
            session.add(rec)
            session.flush()
            ids.append(rec.recommendation_id)
    session.commit()
    return ids
