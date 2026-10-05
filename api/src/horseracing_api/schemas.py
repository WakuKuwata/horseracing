"""Pydantic v2 response schemas (Feature 014) — the OpenAPI contract for the front (015).

Nullable source values (win/top2/top3, odds, pseudo_*) are typed ``float | None`` so a partial row
never raises a validation error. Every odds row carries ``odds_source`` + ``is_estimated`` so the
front cannot conflate real vs estimated; estimated rows are pseudo and carry ``as_of`` (current
recompute time), real rows carry ``updated_at`` (DB latest). selection stays a horse-number array.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Literal

from horseracing_eval.attention_rules import Decision, RuleId, Stage
from pydantic import BaseModel, Field


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
    # Feature 089: raw contribution minus its all-started-horses race mean (v2; null for v1).
    contribution_centered: float | None = None


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
    # Feature 089: raw score relative to the all-started-horses race mean (v2; null for v1).
    score_centered: float | None = None
    # Feature 089: centered contribution outside the stored top-K (v2; null for v1).
    other_contribution_centered: float | None = None
    # Feature 089: all-started prediction-batch size used for centering (v2; null for v1).
    centering_population_size: int | None = None
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


class RaceDispersionDelta(BaseModel):
    """Feature 066 axis A: calibrated-model-p concentration relative to the market's — the neutral
    fact ``H(calibrated p) − H(q)``. Computed at read time when a FROZEN 048 two_gamma calibrator
    is loaded (``DISPERSION_PCAL_PATH``); the calibrator removes the 047 favourite tail-compression
    bias that raw served p would carry. direction is a neutral bucket (model sees the race as open /
    more firm / similar vs the market) — NO buy/edge/value semantics. Null when no calibrator is
    loaded or the field is degenerate. The calibrated p is display-only, never a model feature."""

    normalized_entropy_delta: float | None = None
    direction: Literal["model_more_open", "model_more_firm", "similar"] | None = None
    calibrator_version: str | None = None  # audit (V): which frozen calibrator produced the delta


class RaceDispersion(BaseModel):
    """Feature 066 axis A: race-level decision-support readout of how OPEN (chaotic) the race is,
    summarised from the MARKET vote-share q on the canonical field. NOT a new edge — a read-time
    function of existing q. q is market-derived pseudo/display data (is_pseudo), never a true prob,
    never a model feature. band from FROZEN quintile edges (results never consulted); raw numbers
    shown beside it (favourite win prob / top-3 share / normalised entropy). q missing → available
    false, band/numbers null, NO fallback to model p."""

    available: bool
    unavailable_reason: Literal["no_market_odds", "partial_market_odds"] | None = None
    band: Literal["firm", "somewhat_firm", "standard", "somewhat_open", "open"] | None = None
    normalized_entropy: float | None = None
    favorite_win_prob: float | None = None
    top3_cumulative: float | None = None
    model_delta: RaceDispersionDelta | None = None
    # odds provenance mirrors 021 (final=closing-leaning / prerace); is_pseudo drives front badge.
    odds_as_of: datetime.datetime | None = None
    odds_source: Literal["final", "prerace"] | None = None
    is_pseudo: bool = True
    boundary_version: str | None = None  # null when no boundary artifact loaded (F8)


class UnderratedLongshot(BaseModel):
    """Feature 066 axis B: a horse the MODEL ranks in its top 3 (by p) that the MARKET does NOT
    rank top 3 (popularity_rank > 3). A NEUTRAL FACT (model/market disagree), NOT a buy call."""

    horse_number: int
    popularity_rank: int
    p: float | None = None
    q: float | None = None


class RaceDivergence(BaseModel):
    """Feature 066 axis B: race-level summary of where model p and market q DISAGREE — the material
    for a human's favourite-vs-longshot judgement. NEVER says the model is right (047: q predicts
    better) — it only points at the disagreement (040 discipline). Suppressed (available=false, all
    null) when p/q populations differ (canonical_consistent=false) or q is missing. The per-horse
    040 divergence_band is unchanged and lives on HorsePrediction."""

    available: bool
    summary: str | None = None
    favorite_direction: Literal["model_higher", "model_lower", "similar"] | None = None
    underrated_longshots: list[UnderratedLongshot] = []
    rank_agreement: float | None = None
    model_version: str | None = None  # 057: which selected model's p is being compared


class ChaosSnapshotProvenance(BaseModel):
    """The immutable market observation that produced one Feature 084 readout."""

    model_config = {"extra": "forbid"}

    captured_at: datetime.datetime
    source: str
    seconds_to_post: int | None
    capture_strength: Literal["confirmatory", "weak", "unknown"]
    content_digest: str
    snapshot_id: str


class ChaosEvent(BaseModel):
    """One preregistered top-3-composition event under both market provenances."""

    model_config = {"extra": "forbid"}

    key: Literal["s_ge_20", "himo_are", "total_collapse"]
    label_ja: str
    adjusted_mass: float
    raw_mass: float
    is_structural_zero: bool
    structural_zero_reason: str | None
    lambda_sensitive: bool


class RaceChaosUnavailable(BaseModel):
    """Typed fail-closed state; it never makes the predictions request fail."""

    model_config = {"extra": "forbid"}

    status: Literal["unavailable"]
    unavailable_reason: Literal[
        "no_snapshot",
        "partial_market_odds",
        "invalid_popularity_ranks",
        "field_too_small",
        "field_changed_after_capture",
        "artifact_unavailable",
        "out_of_validity_window",
        "invariant_violation",
    ]
    band_axis: Literal["p_s_ge_20"]


class RaceChaosAvailable(BaseModel):
    """Feature 084 market-derived readout with required, non-null numeric values."""

    model_config = {"extra": "forbid"}

    status: Literal["available"]
    unavailable_reason: None = None
    band: Literal["t3_calm", "t3_mild", "t3_mid", "t3_rough", "t3_wild"]
    band_axis: Literal["p_s_ge_20"]
    field_size: int
    feasible_support: tuple[int, int]
    feasible_support_ja: str
    events: list[ChaosEvent]
    expected_top3_popularity_sum: float
    within_field_size_percentile: float | None
    calibration_status: Literal["provisional", "confirmed"]
    calibration_basis: str
    is_market_derived: Literal[True]
    is_pseudo: Literal[True]
    snapshot: ChaosSnapshotProvenance
    artifact_version: str
    artifact_digest: str
    # FR-020a: a recomputation can never masquerade as the persisted display record. When the
    # source is recomputed, clients can compare this digest with artifact_digest (or see None when
    # the snapshot unexpectedly has no persisted readout).
    readout_source: Literal["persisted", "recomputed"]
    persisted_artifact_digest: str | None


RaceChaos = Annotated[
    RaceChaosAvailable | RaceChaosUnavailable,
    Field(discriminator="status"),
]


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
    # Feature 066 axis A: race-level dispersion (how open the race is) from market q. Additive,
    # nullable. Null only when there is no run at all (typed-empty response).
    race_dispersion: RaceDispersion | None = None
    # Feature 066 axis B: race-level model-vs-market divergence summary (favourite/longshot info).
    race_divergence: RaceDivergence | None = None
    # Feature 084: market-only top-3 chaos readout. It is built before prediction-run selection,
    # so typed-empty prediction responses still carry an available/unavailable tagged state.
    race_chaos: RaceChaos | None = None


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
    # Feature 075: FROZEN market_odds_used = counterfactual snapshot (NOT closing).
    counterfactual_snapshot_gross_return: float | None = None  # frozen odds if hit else 0.0
    counterfactual_snapshot_net_return: float | None = None    # gross - 1
    valuation_basis: str | None = None  # "frozen_snapshot_odds" when settled (provenance, 075)


class FavoriteBaseline(BaseModel):
    """Feature 064: the market baseline (flat-bet the favorite) realised for THIS race. Honest
    reference line — NOT a profit strategy. All-null when unsettled / no priced horse."""
    horse_number: int | None = None
    odds: float | None = None
    settled: bool = False
    hit: bool | None = None
    dead_heat: bool = False
    # Feature 075: favorite baseline is valued on CURRENT race_horses.odds (not a frozen snapshot).
    current_odds_gross_return: float | None = None
    current_odds_net_return: float | None = None
    valuation_basis: str | None = None  # "current_odds" when settled (provenance, 075)


class RecommendationResponse(BaseModel):
    race_id: str
    items: list[RecommendationRow] = []
    # Feature 064: read-time honest-display context (no schema change; derived, never a feature).
    # win_policy_status distinguishes an empty win section: no_run (no prediction) / not_generated
    # (recommend not run) / no_win_selected (win policy ran, selected nothing) / generated.
    win_policy_status: str = "no_run"
    favorite_baseline: FavoriteBaseline | None = None


class ShadowLogMonth(BaseModel):
    model_config = {"extra": "forbid"}  # Feature 075: fail loud on splat key drift (analyze I1)
    month: str
    n_settled: int
    counterfactual_snapshot_recovery: float | None = None  # frozen-snapshot recovery (075)


class ShadowLogResponse(BaseModel):
    """Feature 065: prospective shadow-betting log roll-up (real bettable frozen odds; prospective;
    NOT closing; NOT a profit claim). Empty (n_prospective=0) ⇒ instrument still filling."""
    n_prospective: int = 0
    n_settled: int = 0
    n_hit: int = 0
    hit_rate: float | None = None
    # Feature 075: FROZEN-snapshot recovery (Σ gross_return / n_settled) — NOT closing/realized.
    counterfactual_snapshot_recovery_rate: float | None = None
    valuation_basis: str | None = None  # "frozen_snapshot_odds" (provenance, 075)
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


class JobCaptureRow(BaseModel):
    """Typed projection of the operational capture record in ``summary.capture``."""

    state: Literal["started", "launched", "done"]
    outcome: Literal["captured", "skipped", "rejected", "failed", "unknown"]
    reason: str | None = None
    capture_strength: str | None = None
    confirmation_eligible: bool | None = None
    seconds_to_post: int | None = None
    chaos_snapshot_id: str | None = None


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
    summary: dict | None = None
    capture: JobCaptureRow | None = None


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


# --- Feature 083: typed v1 transcription of the 082 segment-accuracy payload ---------------------
# codex viewer review (P0#1): a verbatim `payload: dict` + hand-written TS casts would MOVE the
# 075 splat-null trap into the admin. The 075 root cause was `Model(**dict)` with extra=ignore +
# defaults — `model_validate` with extra="forbid" and NO silent defaults is the countermeasure,
# not the trap. Every model below forbids unknown keys; the payload's own contract version is
# checked BEFORE validation; a malformed persisted run fails closed (typed 409), never nulls.

class SaCIBlock(BaseModel):
    model_config = {"extra": "forbid"}
    point: float | None
    ci_low: float | None
    ci_high: float | None
    n_days: int
    no_decision: bool
    ci_note: str


class SaReliabilityBin(BaseModel):
    model_config = {"extra": "forbid"}
    lo: float
    hi: float
    n: int
    pred_mean: float | None
    realized: float | None
    wilson_low: float | None
    wilson_high: float | None


class SaCalibrationRace(BaseModel):
    model_config = {"extra": "forbid"}
    grain_note: str
    bins: list[SaReliabilityBin]
    ece: float | None
    #: identically 0 at race grain (Σp=Σy per selected race) — producer nulls it + citl_note
    calibration_in_the_large: None
    citl_note: str
    n: int


class SaCalibrationHorse(BaseModel):
    model_config = {"extra": "forbid"}
    grain_note: str
    bins: list[SaReliabilityBin]
    ece: float | None
    calibration_in_the_large: float | None
    n: int


class SaMarketBlock(BaseModel):
    model_config = {"extra": "forbid"}
    n_market_complete_races: int
    n_total_races: int
    market_nll: float | None = None
    winner_nll_market_subset: float | None = None
    excess_nll_market: float | None = None


class SaByYearRace(BaseModel):
    model_config = {"extra": "forbid"}
    n_races: int
    excess_nll_uniform_point: float


class SaByYearHorse(BaseModel):
    model_config = {"extra": "forbid"}
    n_horses: int
    excess_logloss_point: float


class SaRaceGrainLabel(BaseModel):
    model_config = {"extra": "forbid"}
    winner_nll: Literal["race"]
    calibration: Literal["started_horse_within_selected_races"]


class SaHorseGrainLabel(BaseModel):
    model_config = {"extra": "forbid"}
    excess_logloss: Literal["horse"]
    winner_nll: Literal["NOT_AVAILABLE_AT_HORSE_GRAIN"]


class SaRaceBucket(BaseModel):
    model_config = {"extra": "forbid"}
    grain: SaRaceGrainLabel
    n_races: int
    n_horses: int
    excess_nll_uniform: SaCIBlock
    winner_nll: float
    uniform_nll: float
    market: SaMarketBlock
    by_year: dict[str, SaByYearRace]
    calibration: SaCalibrationRace
    ece_ci: SaCIBlock


class SaHorseBucket(BaseModel):
    model_config = {"extra": "forbid"}
    grain: SaHorseGrainLabel
    n_horses: int
    n_races: int
    excess_logloss_vs_uniform: SaCIBlock
    by_year: dict[str, SaByYearHorse]
    calibration: SaCalibrationHorse
    ece_ci: SaCIBlock


class SaRaceAxis(BaseModel):
    model_config = {"extra": "forbid"}
    axis_id: str
    family: str
    grain: Literal["race"]
    origin: str
    definition: dict          # frozen boundary spec — intentionally schemaless (hash-compared)
    mask_definition_hash: str
    buckets: dict[str, SaRaceBucket]


class SaHorseAxis(BaseModel):
    model_config = {"extra": "forbid"}
    axis_id: str
    family: str
    grain: Literal["horse"]
    origin: str
    definition: dict
    mask_definition_hash: str
    buckets: dict[str, SaHorseBucket]


class SaInstrumentContract(BaseModel):
    model_config = {"extra": "forbid"}
    kind: Literal["segment_accuracy"]
    secondary: Literal[True]
    can_adopt: Literal[False]
    estimand: str
    discovery_rule: str
    ci_note: str
    known_confounds: list[str]
    metric_contract_version: str
    mask_library_version: str
    mask_library_hash: str


class SaProvenance(BaseModel):
    model_config = {"extra": "forbid"}
    base_model_version: str
    feature_version: str
    feature_hash: str | None
    attestation_digest: str
    bundle_digest: str
    prediction_checksum: str
    oof_race_set_hash: str
    scored_race_set_hash: str
    label_snapshot_hash: str
    train_floor: str
    eval_window: list[str]
    first_valid_year: int
    fold_boundaries: list[int] | None
    probability_stage: str
    code_sha: str
    seed: int
    bootstrap_b: int
    metric_contract_version: str
    mask_library_version: str
    mask_library_hash: str


class SaPopulation(BaseModel):
    model_config = {"extra": "forbid"}
    n_scored_races: int
    n_scored_horses: int
    exclusions: dict[str, int]


class SegmentAccuracyPayloadV1(BaseModel):
    model_config = {"extra": "forbid"}
    instrument_contract: SaInstrumentContract
    provenance: SaProvenance
    population: SaPopulation
    axes: list[Annotated[SaRaceAxis | SaHorseAxis, Field(discriminator="grain")]]


class SegmentAccuracyResponse(BaseModel):
    """Feature 083: envelope + typed v1 transcription of the newest 082 run.

    ``diagnostic_run_id`` is the handle the 082 discovery rule requires (a hypothesis found
    here must carry it into its NEW pre-registration)."""

    kind: Literal["segment_accuracy"] = "segment_accuracy"
    diagnostic_run_id: str
    computed_at: datetime.datetime
    date_from: datetime.date | None = None
    date_to: datetime.date | None = None
    logic_version: str
    payload: SegmentAccuracyPayloadV1


# --- Feature 106: purchase records & triple comparison (explicit DTOs, no splat-null) -------


class PurchaseBetView(BaseModel):
    model_config = {"extra": "forbid"}
    bet_type: str
    selection: list[int]
    amount_yen: int
    # pending / settled_real / settled_estimated / refunded / unsettleable
    status: str
    hit: bool | None = None
    payout_yen: float | None = None
    # True only for settled_estimated (double-pseudo; front badges it)
    is_estimated: bool


class PurchaseRecordView(BaseModel):
    model_config = {"extra": "forbid"}
    race_id: str
    race_date: datetime.date
    record_id: str
    kind: str                          # base kind of the effective chain
    result_pending_at_record: bool     # 記録時に結果取込前だった(観測事実 — 発走前の主張ではない)
    recorded_at: datetime.datetime
    n_corrections: int
    was_voided: bool
    bets: list[PurchaseBetView]
    anomalies: list[str]
    note: str | None = None


class PurchaseRecordsResponse(BaseModel):
    model_config = {"extra": "forbid"}
    records: list[PurchaseRecordView]  # chronological by race
    n_races_recorded: int


class ComparisonPoint(BaseModel):
    model_config = {"extra": "forbid"}
    race_id: str
    race_date: datetime.date
    net_yen: float | None = None            # None = この点は算出不能(政策線: snapshot 無し等)
    cumulative_net_yen: float | None = None  # 算出可能点のみの累積


class ComparisonPending(BaseModel):
    model_config = {"extra": "forbid"}
    n_races: int
    n_bets: int
    amount_yen: int


class ComparisonCumulative(BaseModel):
    model_config = {"extra": "forbid"}
    actual: float
    policy: float
    no_bet: float = 0.0
    diff_actual_vs_policy: float | None = None   # None = 政策線が 1 点も算出できない期間
    diff_actual_vs_no_bet: float


class ComparisonCoverage(BaseModel):
    model_config = {"extra": "forbid"}
    overall: float | None = None        # None = 期間内に開催レースが無い(0 除算を偽装しない)
    pre_ingestion: float | None = None
    post_ingestion: float | None = None
    n_all_races: int
    n_recorded_races: int


class ComparisonSeries(BaseModel):
    model_config = {"extra": "forbid"}
    actual: list[ComparisonPoint]
    policy: list[ComparisonPoint]
    no_bet: float = 0.0


class PurchaseComparisonResponse(BaseModel):
    """Feature 106 US2: 実購入 / cap 政策の反実仮想 / 賭けない の三者比較(読み取り時計算)."""

    model_config = {"extra": "forbid"}
    as_of: datetime.datetime
    scope: str                          # all / win_only
    include_post_hoc: bool
    series: ComparisonSeries
    cumulative: ComparisonCumulative
    pending: ComparisonPending
    coverage_rate: ComparisonCoverage
    n_races: int
    n_estimated_settlements: int
    estimated_amount_yen: int
    estimator_provenance: str
    n_post_hoc: int
    n_corrections: int
    n_presentation_unavailable: int
    notes: list[str]


# --- Feature 137: market-aware expected return (期待回収率) ------------------------------
# The values come from a SEPARATE market-aware model (current win odds are among its inputs), so the
# response is independent of the win-probability model selection. win_prob is never exposed
# (constitution IV: the raw binary output is not race-normalized and must not read as a 1着率).
# Strict schemas (extra="forbid", no silent defaults) are the 075 splat-null countermeasure.


class HorseMarketEv(BaseModel):
    """One started horse: expected_return = market-aware p × odds_used (ratio; 1.2 = 120%).

    Pseudo (a model estimate, never a realized return). exceeds_threshold is decided by the API
    (strictly greater than the response's threshold)."""

    model_config = {"extra": "forbid"}

    horse_id: str
    horse_number: int | None
    expected_return: float
    odds_used: float
    exceeds_threshold: bool


class MarketEvAvailable(BaseModel):
    """Stored market-aware expected returns for every started horse of the race + provenance."""

    model_config = {"extra": "forbid"}

    status: Literal["available"]
    race_id: str
    model_version: str
    logic_version: str
    computed_at: datetime.datetime
    #: newest odds observation time across the stored rows
    odds_observed_at: datetime.datetime
    #: the current win odds of at least one horse differ from the odds_used at compute time
    odds_changed_after_compute: bool
    #: every stored row was computed while the race had no result yet
    result_pending_at_compute: bool
    threshold: float
    is_pseudo: Literal[True]
    #: horse_number ascending (nulls last, then horse_id)
    horses: list[HorseMarketEv]


class MarketEvUnavailable(BaseModel):
    """Typed empty state: not computed yet, the started field changed after the compute, or a
    started horse currently has no valid win odds (so the stored values must not be shown)."""

    model_config = {"extra": "forbid"}

    status: Literal["unavailable"]
    race_id: str
    reason: Literal["not_computed", "field_changed", "odds_unavailable"]
    threshold: float


MarketEvResponse = Annotated[
    MarketEvAvailable | MarketEvUnavailable,
    Field(discriminator="status"),
]


# --- Feature 138: 注目条件 (attention conditions) S1-S5 --------------------------------------
# Rule ids and stage names are the eval registry's own Literal types (single definition). Stage
# values are ASCII; the Japanese labels live only in the front's label table (FR-014). No win
# probability and no p̂ anywhere (constitution IV): selected-horse calibration is reported in
# expected-return units only. Strict schemas (extra="forbid", no silent defaults) as in 137.


class StageDetail(BaseModel):
    """Prospective stage of one rule: enough to draw 「300 点不通過」 / 「観察中・判定待ち」."""

    model_config = {"extra": "forbid"}

    stage: Stage
    #: the checkpoint the stage was decided at (300 / 600), None while no decision is recorded
    checkpoint: Literal[300, 600] | None
    #: the counted picks reached a checkpoint whose decision is not recorded (or was recorded under
    #: another prospective start date)
    checkpoint_pending: bool


class EvSnapshot(BaseModel):
    """Expected returns (ratio, pseudo) at one moment. ``judged`` = the frozen pick values;
    ``current`` = the displayed version's latest row (single seed only from the same run)."""

    model_config = {"extra": "forbid"}

    ens_expected_return: float | None
    single_expected_return: float | None
    odds: float | None
    odds_observed_at: datetime.datetime | None
    computed_at: datetime.datetime | None
    run_id: uuid.UUID | None
    is_pseudo: Literal[True]


class AttentionLevels(BaseModel):
    """Axis levels decided by the API (価格鮮度 is computed by the front at render time)."""

    model_config = {"extra": "forbid"}

    backtest: Literal[1, 2, 3]
    prospective: Literal[1, 2, 3]
    price_noise: Literal[1, 2, 3]


class AttentionHorse(BaseModel):
    model_config = {"extra": "forbid"}

    horse_id: str
    horse_number: int | None
    #: rules (rank order, S5 included) with a live (non-voided) pick from the first computation
    applicable: list[RuleId]
    #: the chip's rule (never S5); None when no S1-S4 pick is live
    chip_rule: RuleId | None
    chip_stage: StageDetail | None
    #: S2 sub-chip (only with an S1 chip while S2 also applies and is neither failed nor undecided)
    chip_s2: bool
    #: does the chip's condition still hold on the current values? None when there is no chip
    chip_now: Literal["matches", "no_longer", "unknown"] | None
    judged: EvSnapshot | None
    current: EvSnapshot | None
    #: audit flag: the started field differs from the one the pick was judged on (no effect on
    #: the tally)
    field_changed_after_pick: bool
    #: the chip rule's levels; None when there is no chip
    levels: AttentionLevels | None
    #: stage of every applicable rule
    stages: dict[RuleId, StageDetail]
    pick_status: dict[RuleId, Literal["pick", "void:scratched", "none"]]


class AttentionAvailable(BaseModel):
    """The race's first ensemble computation is recorded; ``horses`` covers every currently
    started horse plus any judged horse that was scratched later (horse_number order)."""

    model_config = {"extra": "forbid"}

    status: Literal["available"]
    race_id: str
    post_time: datetime.datetime | None
    has_results: bool
    #: computed_at of the race's first computation (the scan row)
    judged_at: datetime.datetime
    selection_policy_version: str
    rule_set_version: str
    horses: list[AttentionHorse]


class AttentionUnavailable(BaseModel):
    """No first computation yet: not computed, or a started horse has no valid win odds."""

    model_config = {"extra": "forbid"}

    status: Literal["unavailable"]
    race_id: str
    reason: Literal["not_computed", "odds_unavailable"]


AttentionResponse = Annotated[
    AttentionAvailable | AttentionUnavailable,
    Field(discriminator="status"),
]


class AttentionFrozenStats(BaseModel):
    """Frozen backtest window statistics (closing-odds approximation, ratio units)."""

    model_config = {"extra": "forbid"}

    roi: float
    ci_low: float
    ci_high: float
    p_one_sided: float
    n: int
    hits: int


class AttentionSelectedCalibration(BaseModel):
    """Selected horses: mean expected return vs realized return (expected-return units only)."""

    model_config = {"extra": "forbid"}

    n: int
    mean_ev: float
    realized_roi: float


class AttentionSelectedWindows(BaseModel):
    model_config = {"extra": "forbid"}

    all: AttentionSelectedCalibration
    c: AttentionSelectedCalibration


class AttentionBacktestBootstrap(BaseModel):
    model_config = {"extra": "forbid"}

    impl: str
    b: int
    seed: int
    block: str
    block_universe: str


class AttentionBacktest(BaseModel):
    model_config = {"extra": "forbid"}

    all: AttentionFrozenStats
    c: AttentionFrozenStats
    bets_2024_25_26: list[int]
    selected: AttentionSelectedWindows
    valuation_basis: Literal["closing_odds_approx"]
    #: provenance of the frozen CIs/p (production refreeze — registry ``FROZEN_BOOTSTRAP``)
    bootstrap: AttentionBacktestBootstrap


class AttentionPriceNoise(BaseModel):
    model_config = {"extra": "forbid"}

    sigma: float
    roi: float
    n: int
    overlap: float


class AttentionRuleLevels(BaseModel):
    model_config = {"extra": "forbid"}

    backtest: Literal[1, 2, 3]
    price_noise: Literal[1, 2, 3]


class AttentionCheckpointBootstrap(BaseModel):
    model_config = {"extra": "forbid"}

    impl: str | None
    b: int | None
    seed: int | None
    block_universe: str | None


class CheckpointDecision(BaseModel):
    """One recorded checkpoint decision (append-only; the stage follows these records).

    Only records of the CURRENT selection policy are listed (policy v2: settled at the official
    win payout). ``roi_frozen`` is the recorded ROI (the 0019 column name) under the settlement
    named by ``valuation_basis`` (the record's ``bootstrap.settlement``; null when the record does
    not name a known one)."""

    model_config = {"extra": "forbid"}

    checkpoint: Literal[300, 600]
    decision: Decision
    n_counted: int
    n_hits: int
    roi_frozen: float
    valuation_basis: Literal["official_win_payout", "frozen_pick_odds"] | None
    #: [low, high]; null when the record has no interval (too few race days)
    ci: tuple[float, float] | None
    decided_at: datetime.datetime
    settlement_cutoff: datetime.datetime
    prospective_start_date: datetime.date
    skipped_pending_before_last: int
    counted_pick_ids_sha256: str
    bootstrap: AttentionCheckpointBootstrap


class AttentionOfficialBasis(BaseModel):
    """Settlement at the official win payout (per 100 yen; 139) — the basis of the stages and of
    the prospective level under selection policy v2. A win pays the parimutuel payout, not the
    judged odds, so this figure is what the counted picks actually returned (not approximate)."""

    model_config = {"extra": "forbid"}

    valuation_basis: Literal["official_win_payout"]
    roi: float | None
    ci: tuple[float, float] | None
    p_one_sided: float | None


class AttentionFrozenBasis(BaseModel):
    """Reference: the SAME counted picks settled at the judged odds (odds_used × 100 yen) — policy
    v1's settlement (138 D13), shown alongside the official basis (139 D3/D12). Never the basis of
    a stage under policy v2."""

    model_config = {"extra": "forbid"}

    valuation_basis: Literal["frozen_pick_odds"]
    roi: float | None
    ci: tuple[float, float] | None
    p_one_sided: float | None


class AttentionStoredBasis(BaseModel):
    """Reference settlement at the CURRENT stored win odds (mutable; may be re-ingested).

    Only counted picks whose stored odds are still valid are settled here (``n``); the others are
    left out of numerator and denominator and counted (``n_missing_stored_odds``), never valued at
    the judged odds. ``n + n_missing_stored_odds`` equals the prospective ``n_counted``."""

    model_config = {"extra": "forbid"}

    valuation_basis: Literal["stored_odds_mutable"]
    roi: float | None
    ci: tuple[float, float] | None
    n: int
    n_missing_stored_odds: int


class AttentionProspectiveBootstrap(BaseModel):
    model_config = {"extra": "forbid"}

    impl: str
    b: int
    seed: int
    block: str
    block_universe: str
    rng: str
    numpy_version: str


class AttentionExclusionCounts(BaseModel):
    """Exclusive exclusion classes (eval ``classify_pick``); with n_counted they sum to the rule's
    total pick rows (date-unfiltered, voided picks included)."""

    model_config = {"extra": "forbid"}

    voided_scratched: int
    before_start: int
    post_time_unknown: int
    computed_after_post: int
    result_known_at_compute: int
    observed_after_post: int
    pending_result: int
    #: the race has results but no official win payout row yet (race level)
    payout_race_missing: int
    #: the payout rows disagree with the result (a winner without one / a non-winner with one)
    payout_inconsistent: int
    unsettled_horse: int
    dead_heat: int


class AttentionFlags(BaseModel):
    """Non-exclusive audit flags (counted picks are flagged too; never part of the Σ check)."""

    model_config = {"extra": "forbid"}

    field_changed_after_pick: int


class AttentionFreshnessBand(BaseModel):
    """Counted picks of one band: official payout ROI (the v2 basis) and the judged-odds ROI (the
    v1 reference) of the same picks."""

    model_config = {"extra": "forbid"}

    n: int
    hits: int
    roi_official: float | None
    roi_frozen: float | None


class AttentionJudgedFreshness(BaseModel):
    """Counted picks by 判断時鮮度帯 = post_time − the pick's odds_observed_at."""

    model_config = {"extra": "forbid", "populate_by_name": True}

    le_10m: AttentionFreshnessBand = Field(alias="<=10m")
    le_60m: AttentionFreshnessBand = Field(alias="<=60m")
    gt_60m: AttentionFreshnessBand = Field(alias=">60m")


class AttentionOddsDrift(BaseModel):
    """log(current stored odds / odds_used) over counted picks (diagnostic)."""

    model_config = {"extra": "forbid"}

    n: int
    median_log_ratio: float | None
    p10: float | None
    p90: float | None


class AttentionProspective(BaseModel):
    model_config = {"extra": "forbid"}

    start_date: datetime.date | None
    policy_version: str
    stage: Stage
    checkpoint: Literal[300, 600] | None
    checkpoint_pending: bool
    #: every recorded decision of the rule (current policy and rule set), checkpoint order
    decisions: list[CheckpointDecision]
    next_checkpoint: Literal[300, 600] | None
    remaining_to_next: int | None
    n_counted: int
    n_hits: int
    n_picks_total: int
    #: the stage basis (policy v2): official win payout
    official: AttentionOfficialBasis
    #: reference: the same counted picks at the judged odds (policy v1's settlement)
    frozen: AttentionFrozenBasis
    stored: AttentionStoredBasis
    bootstrap: AttentionProspectiveBootstrap
    counts: AttentionExclusionCounts
    flags: AttentionFlags
    by_judged_freshness: AttentionJudgedFreshness
    odds_drift: AttentionOddsDrift


class AttentionBuyTimeSource(BaseModel):
    """Where the buy-time expectation comes from (registry ``BUY_TIME_EXPECTATION_SOURCE``).
    ``version`` names exactly the served numbers (139 D13: a changed number gets a new version)."""

    model_config = {"extra": "forbid"}

    version: str
    report: str
    period: str
    pairs: int
    races: int
    race_days: int
    method: str
    computed_on: str
    status: str
    #: the independent verification the served numbers passed (a repository path)
    verification: str


class AttentionBuyTimeExpectation(BaseModel):
    """判断時点で買った場合の見込み (139 D6/D13): the past ROI of the horses that matched at the
    judged (pre-race) odds — a conversion from past data, frozen in the registry
    (``BUY_TIME_EXPECTATION``, version ``buy-time-v2``). The prospective record is read against
    this, not against the closing-odds backtest, until it accumulates and replaces it.

    Not one point (the independent verification rejected a 3-digit value): ``range_low`` to
    ``range_high`` is the range of two estimators rounded to 5%, ``ci_low``/``ci_high`` the
    envelope of their 95% CIs and ``interval_includes_100`` whether that interval reaches 100%.
    A rule whose own interval is invalid shows no value: all four numbers null and
    ``included_in`` names the rule whose value covers its horses (S2 ⊂ S1). Served only once
    independently verified (D6) — ``RuleSummary.buy_time_expectation`` is null before that."""

    model_config = {"extra": "forbid"}

    range_low: float | None
    range_high: float | None
    ci_low: float | None
    ci_high: float | None
    interval_includes_100: bool | None
    included_in: RuleId | None
    source: AttentionBuyTimeSource


class RuleSummary(BaseModel):
    model_config = {"extra": "forbid"}

    id: RuleId
    rank: int
    definition_ja: str
    uses_ensemble: bool
    #: strict threshold (EV > ev_gt, ratio)
    ev_gt: float
    #: lo <= odds < hi, or None
    odds_band: tuple[float, float] | None
    #: lo <= days_since_last <= hi, or None
    gap_days: tuple[int, int] | None
    #: found after looking at results (S1/S2: 「探索後固定」)
    posthoc: bool
    #: the comparison condition (S5): never a chip
    control: bool
    backtest: AttentionBacktest
    #: the buy-time expectation (past data, judged-odds selection) the prospective ROI is read
    #: against; null until the registry's value is independently verified (139 D6)
    buy_time_expectation: AttentionBuyTimeExpectation | None
    price_noise: list[AttentionPriceNoise]
    levels: AttentionRuleLevels
    prospective: AttentionProspective


class AttentionRulesResponse(BaseModel):
    model_config = {"extra": "forbid"}

    rule_set_version: str
    #: rank order, fixed (never sorted by results)
    items: list[RuleSummary]
    disclaimer: str


class AttentionDayItem(BaseModel):
    """One chip horse of the day (judged at the race's first computation)."""

    model_config = {"extra": "forbid"}

    race_id: str
    post_time: datetime.datetime | None
    has_results: bool
    venue_code: str | None
    race_number: int | None
    horse_id: str
    horse_number: int | None
    horse_name: str | None
    chip_rule: RuleId
    chip_stage: StageDetail
    chip_s2: bool
    chip_now: Literal["matches", "no_longer", "unknown"]
    stages: dict[RuleId, StageDetail]
    levels: AttentionLevels
    #: odds_observed_at of the displayed version's latest row (null: no current value)
    current_odds_observed_at: datetime.datetime | None


class AttentionDayResponse(BaseModel):
    model_config = {"extra": "forbid"}

    date: datetime.date
    #: post_time ascending (unknown last), then race_id, then horse_number
    items: list[AttentionDayItem]
