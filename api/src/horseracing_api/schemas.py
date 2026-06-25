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
    win: float | None = None
    top2: float | None = None
    top3: float | None = None


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
