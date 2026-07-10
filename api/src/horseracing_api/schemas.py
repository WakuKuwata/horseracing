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
    race_name: str | None = None
    race_class: str | None = None
    distance: int | None = None
    track_type: str | None = None
    # 発走時刻 (post time, JST-aware). netkeiba-sourced; mostly null for JRA-VAN-only races
    # (004 cutoff is date-level). Display-only — never a model feature (leak boundary, II).
    post_time: datetime.datetime | None = None
    # Feature 014: results-confirmed flag — True once race_results rows exist (race run & official),
    # False while result-pending. Lets the list distinguish 確定後 vs 確定前 without a detail fetch.
    has_results: bool = False


class HorseEntry(BaseModel):
    horse_number: int | None = None
    frame: int | None = None
    horse_id: str
    horse_name: str | None = None
    entry_status: str
    age: int | None = None
    sex: str | None = None
    jockey_id: str | None = None      # Feature 029: jockey-profile link (None / nk: = no link)
    jockey_name: str | None = None
    trainer_id: str | None = None     # Feature 029
    trainer_name: str | None = None
    jockey_weight: float | None = None   # 斤量 (carried weight)
    weight: int | None = None            # 馬体重 (body weight)
    weight_diff: int | None = None       # 増減
    odds: float | None = None            # 単勝オッズ (real, latest)
    popularity: int | None = None        # 人気


class RaceDetail(RaceSummary):
    horses: list[HorseEntry]


# --- horse / jockey profiles (Feature 029) ----------------------------------
# Factual career aggregates from race_horses + race_results (NOT model features). Rates use 出走数
# (started) as denominator; finished-only for placings/avg_finish. starts=0 -> rates null (Unknown
# != 0). Pedigree shown by NAME (ids ~0% populated). Read-only; never re-enter model features (II).
class HorseProfile(BaseModel):
    horse_id: str
    horse_name: str | None = None
    sex: str | None = None
    birth_year: int | None = None
    data_source: str | None = None
    sire_name: str | None = None
    dam_name: str | None = None
    damsire_name: str | None = None
    starts: int = 0                      # 出走数 (entry_status='started')
    wins: int = 0                        # 1着
    seconds_in: int = 0                  # 2着以内 (連対)
    shows_in: int = 0                    # 3着以内 (複勝)
    win_rate: float | None = None        # wins / starts (starts=0 -> null)
    quinella_rate: float | None = None   # seconds_in / starts
    show_rate: float | None = None       # shows_in / starts
    avg_finish: float | None = None      # 完走のみの平均着順


class HorseHistoryRow(BaseModel):
    race_id: str
    race_date: datetime.date | None = None
    venue_code: str | None = None
    race_number: int | None = None
    race_name: str | None = None
    race_class: str | None = None
    distance: int | None = None
    track_type: str | None = None
    horse_number: int | None = None
    popularity: int | None = None
    odds: float | None = None
    entry_status: str | None = None
    finish_order: int | None = None
    finish_time_sec: float | None = None   # finish_time (Interval) -> 秒
    last_3f: float | None = None
    result_status: str | None = None


class JockeyProfile(BaseModel):
    jockey_id: str
    jockey_name: str | None = None
    mounts: int = 0                      # 騎乗数 (started)
    wins: int = 0
    seconds_in: int = 0
    shows_in: int = 0
    win_rate: float | None = None
    quinella_rate: float | None = None
    show_rate: float | None = None
    avg_finish: float | None = None


class JockeyHistoryRow(BaseModel):
    race_id: str
    race_date: datetime.date | None = None
    venue_code: str | None = None
    race_number: int | None = None
    race_name: str | None = None
    horse_id: str | None = None
    horse_name: str | None = None
    finish_order: int | None = None
    result_status: str | None = None


# --- predictions ------------------------------------------------------------
class RunAudit(BaseModel):
    prediction_run_id: str
    model_version: str
    logic_version: str
    computed_at: datetime.datetime


class ExplanationItem(BaseModel):
    feature: str
    value: float | str | None = None
    contribution: float


class Explanation(BaseModel):
    """Feature 040: display-only score-contribution explanation (persisted, read as-is).

    Contributions decompose the RAW booster margin (before race-softmax / isotonic / 009), NOT the
    final probability. The front frames this as "score contribution" with limitation notes.
    """

    method: str
    method_version: int
    k: int
    base_value: float
    score: float
    other_contribution: float
    items: list[ExplanationItem]


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
    # Feature 040 US1: persisted score-contribution explanation (read as-is). Null = 未提供.
    explanation: Explanation | None = None
    # Feature 040 US3: neutral factual model-vs-market divergence band. Null = suppressed
    # (q missing or canonical_consistent=false). NO buy/sell/危険/妙味 semantics.
    divergence: Literal["market_higher", "model_higher", "similar"] | None = None


class JointEntry(BaseModel):
    selection: list[int]
    prob: float


class AvailableModel(BaseModel):
    """Feature 057: a model that has a persisted prediction_run for THIS race (i.e. selectable on
    the race-detail view). display_name/purpose are human labels (null until set). is_selected marks
    the model whose run this response returns. adoption_status lets the front badge the active model
    ('active') distinctly from the selected one (selected ≠ adopted)."""

    model_version: str
    display_name: str | None = None
    purpose: str | None = None
    adoption_status: str
    is_selected: bool


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
    # Feature 057: models with a persisted run for this race (deterministic order: active-first →
    # created_at DESC → model_version). Empty = no prediction yet. Additive field (backward compat).
    available_models: list[AvailableModel] = []


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
    recommendation_id: str          # Feature 043: stable row id (front list key; dedup-safe)
    bet_type: str
    selection: list[int]
    stake_fraction: float | None = None   # Feature 043: Kelly effective fraction (016); NULL=flat
    market_odds_used: float | None = None
    estimated_market_odds_used: float | None = None
    is_estimated_odds: bool
    pseudo_odds: float | None = None
    pseudo_roi: float | None = None
    double_pseudo: bool
    logic_version: str
    computed_at: datetime.datetime
    prediction_run_id: str
    # Feature 049: retrospective WIN backtest (real odds, NOT pseudo). win-only; null otherwise.
    settled: bool = False              # race has an official result
    hit: bool | None = None            # recommended horse finished 1st (null = void / unsettled)
    dead_heat: bool = False            # 1st was a dead heat (real dividend is split)
    realized_return: float | None = None  # per-unit payout multiple: real odds if hit else 0.0
    realized_roi: float | None = None      # realized_return - 1


class FavoriteBaseline(BaseModel):
    """Feature 064: the market baseline (flat-bet the favorite) realised for THIS race. Honest
    reference line — NOT a profit strategy. All-null when unsettled / no priced horse."""
    horse_number: int | None = None
    odds: float | None = None
    settled: bool = False
    hit: bool | None = None
    dead_heat: bool = False
    realized_return: float | None = None
    realized_roi: float | None = None


class RecommendationResponse(BaseModel):
    race_id: str
    items: list[RecommendationRow] = []
    # Feature 064: read-time honest-display context (no schema change; derived, never a feature).
    # win_policy_status distinguishes an empty win section: no_run (no prediction) / not_generated
    # (recommend not run) / no_win_selected (win policy ran, selected nothing) / generated.
    win_policy_status: str = "no_run"
    favorite_baseline: FavoriteBaseline | None = None


class ShadowLogMonth(BaseModel):
    month: str
    n_settled: int
    recovery: float | None = None


class ShadowLogResponse(BaseModel):
    """Feature 065: prospective shadow-betting log roll-up (real bettable frozen odds; prospective;
    NOT closing; NOT a profit claim). Empty (n_prospective=0) ⇒ instrument still filling."""
    n_prospective: int = 0
    n_settled: int = 0
    n_hit: int = 0
    hit_rate: float | None = None
    recovery_rate: float | None = None
    n_pending: int = 0
    n_void: int = 0
    weak_pretime: int = 0
    by_month: list[ShadowLogMonth] = []
    first_at: str | None = None
    last_at: str | None = None


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


class ImportanceValue(BaseModel):
    feature: str
    gain: float


class ImportanceResponse(BaseModel):
    """Feature 040 US2: split-gain feature importance (display-only, read from metrics_summary).

    ``type`` is "gain" — split-gain importance, biased toward high-gain-split features. The front
    labels it narrowly ("分割利得(gain)重要度"), not general feature importance.
    """

    model_version: str
    type: str = "gain"
    values: list[ImportanceValue] = []


# --- model registry (Feature 051 admin console, read-only) --------------------------------------
class ModelVersionRow(BaseModel):
    """Feature 051: one model_versions row for the admin registry — persisted values ONLY
    (metrics_summary transcription, no recomputation = 021 discipline). Missing keys → null
    (old models lack train_through — recorded since 050 — and pre-040 runs lack importance)."""

    model_version: str
    model_family: str | None = None
    feature_version: str | None = None
    label_schema: str
    adoption_status: str
    created_at: datetime.datetime
    # Feature 057: human-readable purpose metadata (null until set via set-model-label CLI).
    display_name: str | None = None
    purpose: str | None = None
    # eval overall (win) — OOS walk-forward persisted by the training harness
    win_log_loss: float | None = None
    win_auc: float | None = None
    win_ece: float | None = None
    win_brier: float | None = None
    # training metadata (050: train_through/n_model_rows recorded at train time)
    objective: str | None = None
    calibration: str | None = None
    train_through: str | None = None
    n_model_rows: int | None = None
    git_sha: str | None = None
    adopted: bool | None = None          # adoption-gate verdict at save time
    # whether the per-model detail endpoints have content (021 calibration / 040 importance)
    has_calibration: bool = False
    has_importance: bool = False


class ModelListResponse(BaseModel):
    items: list[ModelVersionRow] = []


# --- coverage / jobs (Feature 052 admin console, read-only) --------------------------------------
class CoverageDay(BaseModel):
    """One race day's product coverage. n_predicted_active uses the ACTIVE model only (044
    idempotency semantics); 0 when no model is active."""

    date: datetime.date
    n_races: int
    n_with_odds: int
    n_with_results: int
    n_predicted_active: int
    n_with_recommendations: int


class CoverageResponse(BaseModel):
    date_from: datetime.date
    date_to: datetime.date
    active_model_version: str | None = None  # null = no active model (predicted counts are 0)
    days: list[CoverageDay] = []


class JobRow(BaseModel):
    """One ingestion_jobs row (audit trail; read-only transcription)."""

    ingestion_job_id: str
    source: str | None = None
    job_type: str | None = None
    scope: str | None = None
    scope_value: str | None = None
    status: str
    trace_id: str | None = None
    retry_count: int
    started_at: datetime.datetime | None = None
    completed_at: datetime.datetime | None = None
    error_message: str | None = None
    processed_rows: int | None = None
    skipped_rows: int | None = None
    error_count: int | None = None
    created_at: datetime.datetime


class JobListResponse(BaseModel):
    items: list[JobRow] = []


# --- diagnostics (Feature 054 admin console, read-only transcription) ----------------------------
class SegmentEdgeRow(BaseModel):
    """One 047 segment row — verbatim from the persisted payload (no derived metrics)."""

    axis: str
    segment: str
    n: int
    win_rate: float
    logloss_p: float
    logloss_q: float
    gap: float          # logloss_p − logloss_q; positive = the market is better here
    mean_p: float
    mean_q: float


class SegmentEdgeResponse(BaseModel):
    kind: str = "segment_edge"
    computed_at: datetime.datetime
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    logic_version: str
    n_horses: int
    note: str            # 047 standing disclaimer (SECONDARY, pre-registered, not a buy signal)
    rows: list[SegmentEdgeRow] = []
