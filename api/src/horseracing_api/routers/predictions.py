"""predictions router (Feature 014): /races/{race_id}/predictions.

Deterministic run selection + per-horse win/top2/top3 + audit. Joint probabilities ONLY when a
bet_type is given (never a full grid unprompted): computed on the canonical population, serialized
with db.canonical_selection (parity with 011/012), ordered by (-prob, selection), capped to top-K.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from horseracing_db.enums import BetType
from horseracing_db.selection import canonical_selection
from horseracing_probability.engine import joint_probabilities
from sqlalchemy.orm import Session

from ..deps import get_session
from ..dispersion import (
    build_race_dispersion,
    build_race_divergence,
    load_boundary,
    load_p_calibrator,
)
from ..queries import (
    available_models_for_race,
    get_race,
    prior_start_counts,
    race_has_results,
    run_predictions,
    win_odds_as_of,
)
from ..schemas import (
    AvailableModel,
    Explanation,
    HorsePrediction,
    JointEntry,
    PredictionResponse,
    RunAudit,
)
from ..selection import (
    canonical_win_probs,
    divergence_band,
    market_win_probs,
    select_prediction_run,
)

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")
_EXOTIC = set(BetType.EXOTIC)


def _prior_starts_band(prior_starts: int) -> str:
    """Feature 021 US3: NEUTRAL factual prior-start volume band (few/some/many). codex: not a
    calibration/confidence claim — just how much race history backs this horse."""
    if prior_starts <= 1:
        return "few"
    if prior_starts <= 5:
        return "some"
    return "many"


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def _joint_map(jp, bet_type: str):
    return {
        BetType.PLACE: jp.place, BetType.QUINELLA: jp.quinella, BetType.EXACTA: jp.exacta,
        BetType.WIDE: jp.wide, BetType.TRIO: jp.trio, BetType.TRIFECTA: jp.trifecta,
    }.get(bet_type)


def _numbers(key) -> list[int]:
    return [int(key)] if isinstance(key, int) else [int(x) for x in key]


@router.get("/races/{race_id}/predictions", response_model=PredictionResponse, tags=["predictions"])
def predictions(
    race_id: str,
    bet_type: str | None = Query(default=None),
    top: int = Query(default=20, ge=1, le=200),
    model_version: str | None = Query(default=None),
    session: Session = Depends(get_session),
):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    if get_race(session, race_id) is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    if bet_type is not None and bet_type not in _EXOTIC:
        return _err(422, "invalid_bet_type", f"bet_type must be one of {sorted(_EXOTIC)}")

    # Feature 057: optional model_version selects that model's run (else active). No silent
    # fallback: a specified model with no run for this race is a typed 404, not the active model's.
    run = select_prediction_run(session, race_id, model_version)
    if run is None:
        if model_version is not None:
            return _err(
                404, "prediction_unavailable",
                f"model {model_version} has no prediction for race {race_id}",
            )
        return PredictionResponse(race_id=race_id, run=None, horses=[])  # typed-empty (no runs)

    # Feature 021 US1: market vote-share q on the SAME canonical field as model p (R1). p_numbers is
    # the 009 canonical population; q is computed/renormalized on it. p≠q kept separate; q null when
    # the horse has no valid win odds (never 0-filled).
    pmap = canonical_win_probs(session, run_id=run.prediction_run_id, race_id=race_id)
    qmap, canonical_consistent = market_win_probs(
        session, race_id=race_id, p_numbers=set(pmap)
    )
    # US3: leak-safe prior-start count (strictly before this race); absent -> 0 = few.
    backing = prior_start_counts(session, race_id)

    horses = []
    for (n, hid, _status, w, t2, t3, exp) in run_predictions(
        session, run_id=run.prediction_run_id, race_id=race_id
    ):
        p_val = float(w) if w is not None else None
        q_val = qmap.get(int(n)) if n is not None else None
        # Feature 040 US3: divergence suppressed unless p and q share the canonical field.
        div = divergence_band(p_val, q_val if canonical_consistent else None)
        horses.append(
            HorsePrediction(
                horse_number=n, horse_id=hid,
                win=p_val,
                top2=(float(t2) if t2 is not None else None),
                top3=(float(t3) if t3 is not None else None),
                market_win_prob=q_val,
                prior_starts_band=_prior_starts_band(backing.get(hid, 0)),
                # Feature 040 US1: persisted explanation read as-is (JSONB dict → typed model).
                explanation=(Explanation.model_validate(exp) if exp else None),
                divergence=div,
            )
        )
    audit = RunAudit(
        prediction_run_id=str(run.prediction_run_id), model_version=run.model_version,
        logic_version=run.logic_version, computed_at=run.computed_at,
    )
    # Feature 057: models with a run for this race (for the front selector). is_selected marks the
    # model this response returned. Deterministic order comes from the query (active-first → …).
    available = [
        AvailableModel(
            model_version=m.model_version, display_name=m.display_name, purpose=m.purpose,
            adoption_status=m.adoption_status, is_selected=(m.model_version == run.model_version),
        )
        for m in available_models_for_race(session, race_id)
    ]
    odds_as_of = win_odds_as_of(session, race_id)
    odds_source = "final" if race_has_results(session, race_id) else "prerace"
    # Feature 066 axis A: race-level dispersion from market q on the SAME canonical field. Band from
    # a frozen boundary artifact (omitted when absent, F8); raw numbers always shown when q covers
    # the field. q missing/partial → unavailable, NO fallback to model p. model_delta = calibrated-p
    # vs q concentration (frozen two_gamma calibrator, omitted when absent); band stays q-only.
    race_dispersion = build_race_dispersion(
        qmap=qmap, pmap=pmap,
        odds_as_of=odds_as_of, odds_source=odds_source,
        boundary=load_boundary(), p_calibrator=load_p_calibrator(),
    )
    # Feature 066 axis B: neutral p-vs-q divergence summary (suppressed when canonical_consistent
    # is false / q missing). model_version records which selected model's p is compared (057).
    race_divergence = build_race_divergence(
        pmap=pmap, qmap=qmap, canonical_consistent=canonical_consistent,
        model_version=run.model_version,
    )
    resp = PredictionResponse(
        race_id=race_id, run=audit, horses=horses,
        market_prob_source="win_odds_vote_share",
        canonical_consistent=canonical_consistent,
        odds_as_of=odds_as_of,
        odds_source=odds_source,
        available_models=available,
        race_dispersion=race_dispersion,
        race_divergence=race_divergence,
    )

    if bet_type is None:
        return resp  # no joint without an explicit bet_type (grid protection)

    canon = pmap  # reuse the canonical p population computed above
    if len(canon) < 2:
        return _err(409, "no_usable_probabilities", "no usable win probabilities for joint")
    jp = joint_probabilities(canon, field_size=len(canon))  # may raise -> 409 via app handler
    jmap = _joint_map(jp, bet_type)
    if not jmap:  # field rule (place/wide for tiny fields) or N<3
        resp.joint = []
        resp.joint_bet_type = bet_type
        return resp
    entries = [
        JointEntry(selection=canonical_selection(bet_type, _numbers(key)), prob=float(p))
        for key, p in jmap.items() if p is not None
    ]
    entries.sort(key=lambda e: (-e.prob, e.selection))
    resp.joint = entries[:top]
    resp.joint_bet_type = bet_type
    resp.joint_logic_version = "joint=PL/Harville(009);canonical_started"
    return resp
