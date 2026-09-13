"""Deterministic prediction-run selection + canonical win-prob population (Feature 014).

A race may have several prediction_runs across model versions. We pick deterministically: the run
whose model is adopted (``adoption_status='active'``) first, then most recent ``computed_at``, then
highest ``prediction_run_id`` — a total order. PredictionRun has no adoption_status column, so we
JOIN model_versions. The chosen run_id is returned to the caller for the audit envelope. Canonical
win probs require the saved run's horse IDs to match today's started field (constitution IV).
"""

from __future__ import annotations

from horseracing_db.enums import AdoptionStatus, EntryStatus
from horseracing_db.models import ModelVersion, PredictionRun, RaceHorse, RacePrediction
from horseracing_probability.market_odds import market_implied_win_probs
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .queries import canonical_win_odds


def prediction_population_matches(session: Session, *, run_id, race_id: str) -> bool:
    """A saved run is usable only for the exact, nonempty field it predicted.

    Dropping a subsequently cancelled horse would leave the persisted win/top2/top3 on the
    old field. Require a new prediction instead; neither head scaling nor an older-run fallback
    can recover the model's inference and calibration on the changed field.
    """
    started = select(RaceHorse.horse_id).where(
        RaceHorse.race_id == race_id, RaceHorse.entry_status == EntryStatus.STARTED,
    )
    predicted = select(RacePrediction.horse_id).where(
        RacePrediction.prediction_run_id == run_id,
    )
    unexpected = predicted.where(RacePrediction.horse_id.not_in(started)).exists()
    missing = started.where(RaceHorse.horse_id.not_in(predicted)).exists()
    return session.scalar(
        select(PredictionRun.prediction_run_id).where(
            PredictionRun.prediction_run_id == run_id,
            PredictionRun.race_id == race_id,
            started.exists(), ~unexpected, ~missing,
        )
    ) is not None


def select_prediction_run(
    session: Session, race_id: str, model_version: str | None = None
) -> PredictionRun | None:
    """Deterministic run selection.

    ``model_version=None`` (default, Feature 014): active model → computed_at DESC →
    prediction_run_id DESC — unchanged, backward compatible.

    ``model_version`` given (Feature 057): restrict to that model's runs, computed_at DESC →
    prediction_run_id DESC (the active-first tie-break does NOT apply — active status must not
    affect which model is selected). Returns None when that model has no run (caller → 404).
    The selected run must match the current started field; if stale, return None without falling
    back to an older run or a different model. A fresh prediction restores availability.
    """
    recency = (PredictionRun.computed_at.desc(), PredictionRun.prediction_run_id.desc())
    if model_version is not None:
        run = session.scalars(
            select(PredictionRun)
            .where(PredictionRun.race_id == race_id)
            .where(PredictionRun.model_version == model_version)
            .order_by(*recency)
        ).first()
    else:
        active_first = case((ModelVersion.adoption_status == AdoptionStatus.ACTIVE, 0), else_=1)
        run = session.scalars(
            select(PredictionRun)
            .join(ModelVersion, PredictionRun.model_version == ModelVersion.model_version)
            .where(PredictionRun.race_id == race_id)
            .order_by(active_first, *recency)
        ).first()
    if run is None or not prediction_population_matches(
        session, run_id=run.prediction_run_id, race_id=race_id,
    ):
        return None
    return run


def canonical_win_probs(session: Session, *, run_id, race_id: str) -> dict[int, float]:
    """{horse_number -> win_prob} for STARTED horses with positive win_prob (009 input pop).

    A changed or incomplete field returns empty, including for direct run-ID callers.
    For a matching field, non-positive/None probs are dropped; the 009 engine renormalizes.
    """
    if not prediction_population_matches(session, run_id=run_id, race_id=race_id):
        return {}
    rows = session.execute(
        select(RaceHorse.horse_number, RacePrediction.win_prob)
        .join(RacePrediction, RacePrediction.horse_id == RaceHorse.horse_id)
        .where(RaceHorse.race_id == race_id)
        .where(RacePrediction.prediction_run_id == run_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    out: dict[int, float] = {}
    for horse_number, win_prob in rows:
        if horse_number is None or win_prob is None or float(win_prob) <= 0.0:
            continue
        out[int(horse_number)] = float(win_prob)
    return out


def market_win_probs(
    session: Session, *, race_id: str, p_numbers: set[int]
) -> tuple[dict[int, float], bool]:
    """Feature 021 US1: market vote-share q on the SAME canonical field as model p.

    Returns ({horse_number -> q}, canonical_consistent). q is `market_implied_win_probs` (010) over
    started horses with valid win odds, renormalized on that population. ``canonical_consistent`` is
    True only when the q population exactly matches the model-p population (``p_numbers``) — when it
    differs, the per-horse p−q divergence is mathematically incomparable and the front must suppress
    it (R1 / 憲法 IV). q is pseudo (NOT a true prob, NOT p) and never re-enters model features.
    """
    odds = canonical_win_odds(session, race_id)  # {horse_number -> win odds} (started, >0)
    q = market_implied_win_probs(odds) if odds else {}
    q = {int(k): float(v) for k, v in q.items()}
    consistent = bool(p_numbers) and set(q.keys()) == p_numbers
    return q, consistent


# Feature 040 US3: pre-registered divergence bands (FR-011). p, q are same-canonical-field win
# probs. RELATIVE floor max(0.03, 0.5*q) avoids badge spam where q is tiny. Boundaries (equality)
# fall into "similar". NEUTRAL FACTUAL only — no buy/sell/危険/妙味 semantics, no sorting.
DIVERGENCE_ABS_FLOOR = 0.03
DIVERGENCE_REL_FRAC = 0.5


def divergence_band(p: float | None, q: float | None) -> str | None:
    """Neutral model-vs-market band, or None (suppressed) when p or q is missing.

    Callers additionally suppress (pass q=None) when canonical_consistent is false.
    """
    if p is None or q is None:
        return None
    margin = max(DIVERGENCE_ABS_FLOOR, DIVERGENCE_REL_FRAC * q)
    if p < q - margin:
        return "market_higher"  # 市場評価がモデルより高い
    if p > q + margin:
        return "model_higher"   # モデル評価が市場より高い
    return "similar"
