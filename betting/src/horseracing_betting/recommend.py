"""Generate single-win EV recommendations from a prediction run (contracts/recommend.md).

Reads the run's race_predictions (win prob) joined with race_horses (odds / horse_number /
entry_status), selects EV>=threshold bets (renormalized over started), and appends
recommendations rows. Append-only: re-running adds a new group (distinguished by logic_version).
Bet selection never reads race_results (leak boundary).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from horseracing_db.enums import BetType
from horseracing_db.models import PredictionRun, RaceHorse, RacePrediction, Recommendation
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import BETTING_LOGIC_VERSION
from .ev import select_ev_bets
from .kelly_allocation import allocate_kelly
from .kelly_sizing import single_kelly
from .kelly_types import KellyConfig

DEFAULT_THRESHOLD = 1.0
DEFAULT_STAKE = 100.0


def default_logic_version(
    threshold: float, stake: float, *, cfg: KellyConfig | None = None
) -> str:
    lv = (
        f"ev=win_prob*odds;thr={threshold};stake={stake};"
        f"excl=scratch+nullodds+zeroprob;renorm=started;v={BETTING_LOGIC_VERSION}"
    )
    if cfg is not None:  # Feature 045: Kelly sizing recorded so stake reproduces (V)
        lv += (
            f";kelly=lam_real={cfg.lambda_real};cap_bet={cfg.cap_bet};"
            f"cap_total={cfg.cap_total};alloc={cfg.allocation};bankroll={cfg.bankroll}"
        )
    return lv


def _load_horses(session: Session, prediction_run_id, race_id: str) -> list[dict]:
    probs = dict(
        session.execute(
            select(RacePrediction.horse_id, RacePrediction.win_prob).where(
                RacePrediction.prediction_run_id == prediction_run_id
            )
        ).all()
    )
    horses: list[dict] = []
    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == race_id)):
        wp = probs.get(rh.horse_id)
        horses.append(
            {
                "horse_id": rh.horse_id,
                "horse_number": rh.horse_number,
                "win_prob": float(wp) if wp is not None else None,
                "odds": float(rh.odds) if rh.odds is not None else None,
                "entry_status": rh.entry_status,
            }
        )
    return horses


def _win_stake_fractions(bets, cfg: KellyConfig) -> list[float | None]:
    """Feature 045: Kelly stake per win bet (016 pure functions; real odds → is_estimated=False).

    Same-(race, win) bets are mutually exclusive (one winner) → one allocation group.
    A bet filtered out by single_kelly (edge≤floor / odds<o_min) gets None (flat display).
    """
    sized: list[tuple[int, float, float, float]] = []   # (index, p, odds, raw_f)
    for i, b in enumerate(bets):
        s = single_kelly(b.win_prob, b.odds, is_estimated=False, cfg=cfg)
        if s is not None:
            sized.append((i, b.win_prob, b.odds, s[1]))
    fractions: list[float | None] = [None] * len(bets)
    if sized:
        alloc = allocate_kelly([(p, o, False, raw) for (_i, p, o, raw) in sized], cfg=cfg)
        for (i, _p, _o, _raw), frac in zip(sized, alloc, strict=True):
            if frac > 0.0:
                fractions[i] = frac
    return fractions


def generate_recommendations(
    session: Session,
    *,
    prediction_run_id,
    threshold: float = DEFAULT_THRESHOLD,
    stake: float = DEFAULT_STAKE,
    logic_version: str | None = None,
    cfg: KellyConfig | None = None,
) -> list[uuid.UUID]:
    run = session.get(PredictionRun, prediction_run_id)
    if run is None:
        raise ValueError(f"prediction_run {prediction_run_id} not found")
    lv = logic_version or default_logic_version(threshold, stake, cfg=cfg)

    horses = _load_horses(session, prediction_run_id, run.race_id)
    bets = select_ev_bets(horses, threshold=threshold, stake=stake)
    # Feature 045: opt-in Kelly sizing (cfg=None keeps the original flat behaviour, NULL stake).
    fractions = _win_stake_fractions(bets, cfg) if cfg is not None else [None] * len(bets)

    ids: list[uuid.UUID] = []
    for b, frac in zip(bets, fractions, strict=True):
        rec = Recommendation(
            prediction_run_id=prediction_run_id,
            race_id=run.race_id,
            bet_type=BetType.WIN,
            selection={"horse_id": b.horse_id, "horse_number": b.horse_number},
            market_odds_used=Decimal(str(b.odds)),
            estimated_market_odds_used=None,
            is_estimated_odds=False,
            pseudo_odds=Decimal(str(1.0 / b.win_prob)),     # model-implied odds
            pseudo_roi=Decimal(str(b.ev - 1.0)),            # decision-time expected ROI
            stake_fraction=(Decimal(str(frac)) if frac is not None else None),
            logic_version=lv,
        )
        session.add(rec)
        session.flush()
        ids.append(rec.recommendation_id)
    session.commit()
    return ids
