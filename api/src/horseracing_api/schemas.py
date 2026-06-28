"""Pydantic v2 response schemas (Feature 014) — the OpenAPI contract for the front (015).

Nullable source values (win/top2/top3, odds, pseudo_*) are typed ``float | None`` so a partial row
never raises a validation error. Every odds row carries ``odds_source`` + ``is_estimated`` so the
front cannot conflate real vs estimated; estimated rows are pseudo and carry ``as_of`` (current
recompute time), real rows carry ``updated_at`` (DB latest). selection stays a horse-number array.
"""

from __future__ import annotations

import datetime
from typing import Literal

from pydantic import BaseModel


class ErrorBody(BaseModel):
    status: int
    code: str
    detail: str


class Page[T](BaseModel):
    items: list[T]
    page: int
    page_size: int
    total: int
    has_next: bool


# --- races ------------------------------------------------------------------
class RaceSummary(BaseModel):
    race_id: str
    race_date: datetime.date | None = None
    venue_code: str | None = None
    race_number: int | None = None
    race_class: str | None = None
    distance: int | None = None
    track_type: str | None = None


class HorseEntry(BaseModel):
    horse_number: int | None = None
    horse_id: str
    entry_status: str
    age: int | None = None
    sex: str | None = None


class RaceDetail(RaceSummary):
    horses: list[HorseEntry]


# --- predictions ------------------------------------------------------------
class RunAudit(BaseModel):
    prediction_run_id: str
    model_version: str
    logic_version: str
    computed_at: datetime.datetime


class HorsePrediction(BaseModel):
    horse_number: int | None = None
    horse_id: str
    win: float | None = None        # model p (009 canonical field)
    top2: float | None = None
    top3: float | None = None
    # Feature 021 US1: market-implied win prob q (vote-share), computed on the SAME canonical field
    # as p. Pseudo (estimate, contains favorite-longshot bias, NOT a true prob, NOT model p). Null
    # when the horse has no valid win odds (never 0-filled). Kept SEPARATE from win (p≠q).
    market_win_prob: float | None = None
    # Feature 021 US3: NEUTRAL FACTUAL prior-start volume band (few <=1 / some 2-5 / many >=6).
    # codex: the T016 calibration-trust margin was too thin (+0.00011 over gate) to claim "less
    # reliable", so this ships ONLY as a factual history-volume hint (NOT a confidence/calibration
    # signal): no weak/strong wording, no colour, no sorting. Null if absent.
    prior_starts_band: Literal["few", "some", "many"] | None = None


class JointEntry(BaseModel):
    selection: list[int]
    prob: float


class PredictionResponse(BaseModel):
    race_id: str
    run: RunAudit | None = None
    horses: list[HorsePrediction] = []
    joint: list[JointEntry] | None = None
    joint_bet_type: str | None = None
    joint_logic_version: str | None = None
    # Feature 021 US1: market-q provenance + canonical-field consistency + odds audit. q is pseudo
    # (vote-share); canonical_consistent=False means p and q populations differ -> front must
    # suppress the p−q divergence (R1). odds_source: final (race has results) vs prerace.
    market_prob_source: Literal["win_odds_vote_share"] | None = None
    canonical_consistent: bool | None = None
    odds_as_of: datetime.datetime | None = None
    odds_source: Literal["final", "prerace"] | None = None


# --- odds (real vs estimated kept in SEPARATE fields) -----------------------
class WinOddsRow(BaseModel):
    horse_number: int | None = None
    horse_id: str
    odds: float | None = None
    odds_source: Literal["real"] = "real"
    is_estimated: Literal[False] = False
    updated_at: datetime.datetime | None = None


class EstimatedOddsRow(BaseModel):
    bet_type: str
    selection: list[int]
    odds: float | None = None
    odds_source: Literal["estimated"] = "estimated"
    is_estimated: Literal[True] = True
    pseudo: Literal[True] = True
    as_of: datetime.datetime


class RealExoticOddsRow(BaseModel):
    bet_type: str
    selection: list[int]
    odds: float | None = None
    odds_source: Literal["real"] = "real"
    is_estimated: Literal[False] = False
    coverage_scope: str | None = None
    updated_at: datetime.datetime | None = None


class OddsResponse(BaseModel):
    race_id: str
    win: list[WinOddsRow] = []
    estimated: list[EstimatedOddsRow] = []
    real_exotic: list[RealExoticOddsRow] = []


# --- recommendations (persisted SELECT only, exotic bet types only) ---------
class RecommendationRow(BaseModel):
    bet_type: str
    selection: list[int]
    market_odds_used: float | None = None
    estimated_market_odds_used: float | None = None
    is_estimated_odds: bool
    pseudo_odds: float | None = None
    pseudo_roi: float | None = None
    double_pseudo: bool
    logic_version: str
    computed_at: datetime.datetime
    prediction_run_id: str


class RecommendationResponse(BaseModel):
    race_id: str
    items: list[RecommendationRow] = []


# --- calibration / reliability (Feature 021 US2, walk-forward OOS, read-only) ----------------
class CalibrationBin(BaseModel):
    pred_lo: float
    pred_hi: float
    pred_mean: float | None = None
    realized_rate: float | None = None
    realized_ci_low: float | None = None   # Wilson interval (count-aware, FR-006b)
    realized_ci_high: float | None = None
    count: int
    suppressed: bool = False               # too few samples -> not plotted (R5)


class CalibrationResponse(BaseModel):
    model_version: str
    oos: bool = True                       # walk-forward OOS only (never in-sample, R2)
    source: Literal["walk_forward_oos"] = "walk_forward_oos"
    label: str = "win"
    valid_years: list[int] = []
    n_total: int = 0
    ece: float | None = None
    bins: list[CalibrationBin] = []
