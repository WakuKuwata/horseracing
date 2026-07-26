"""Fit and exclusively publish the Feature 084 chaos-band artifact.

This is the orchestration layer that may depend on both ``eval`` (market-q
lambda fitting) and ``probability`` (the ordered-triple chaos readout).  The
probability package owns artifact loading; this module owns only fit/publish.
"""

from __future__ import annotations

import bisect
import datetime
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.chaos_lambda import (
    MarketLambdaSample,
    fit_chaos_lambda,
)
from horseracing_eval.dispersion_bands import fit_quintile_edges, normalized_entropy
from horseracing_eval.harness import reliability_bins
from horseracing_eval.metrics import auc_label, brier_label, log_loss_label
from horseracing_eval.stage_discount import DEFAULT_MIN_RACES, StageDiscount
from horseracing_probability.chaos_distribution import (
    ChaosInvariantError,
    chaos_readout,
)
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1, EventDefinition
from horseracing_probability.market_odds import market_implied_win_probs

ARTIFACT_VERSION = "chaosbands-v1"
LABEL_DEFINITION = "top3_popularity_composition_proxy_v1"
BAND_AXIS = "p_s_ge_20"
DEFAULT_FINAL_DECISION_DATE = datetime.date(2027, 12, 31)
_REFERENCE_QUANTILE_LEVELS = tuple(index / 20 for index in range(21))
_PROVISIONAL_EDGES = (0.2, 0.4, 0.6, 0.8)
DIAGNOSTIC_BOOTSTRAP_SEED = 20260726
DIAGNOSTIC_BOOTSTRAP_B = 2000
DIAGNOSTIC_CI_NOTE = "pointwise 95% CI; multiple comparisons not adjusted"
PROSPECTIVE_BOOTSTRAP_SEED = 20260726
PROSPECTIVE_BOOTSTRAP_B = 2000
_BAND_IDS = ("t3_calm", "t3_mild", "t3_mid", "t3_rough", "t3_wild")
_FIELD_SIZE_BUCKETS = (
    ("4-8", 4, 8),
    ("9-11", 9, 11),
    ("12-13", 12, 13),
    ("14-15", 14, 15),
    ("16-18", 16, 18),
    ("19+", 19, None),
)
_SCORE_CLIP = 1e-15
_POST_TIME_HISTORICAL_REFERENCE = {
    "2024": 0.0,
    "2025": 0.229,
    "2026": 1.0,
}
_CAPTURE_HORIZON_BUCKETS = (
    ("0-9m", 0, 599),
    ("10-29m", 600, 1799),
    ("30-59m", 1800, 3599),
    ("60m+", 3600, None),
)
_REQUIRED_SAMPLE_ESTIMATES = {
    "s_ge_20": {
        "role": "controls_promotion",
        "reference_positive_rate": 0.093,
        "independent_races": 1076,
        "cluster_adjusted_races": [1613, 2689],
        "years": [0.5, 0.8],
    },
    "himo_are": {
        "role": "secondary",
        "reference_positive_rate": 0.105,
        "independent_races": 953,
        "cluster_adjusted_races": [1429, 2381],
        "years": [0.4, 0.7],
    },
    "total_collapse": {
        "role": "not_eligible_lambda_insensitive",
        "reference_positive_rate": 0.039,
        "independent_races": 2565,
        "cluster_adjusted_races": [3847, 6411],
        "years": [1.1, 1.9],
    },
    "s_ge_30": {
        "role": "diagnostic_only",
        "reference_positive_rate": 0.0064,
        "independent_races": 15625,
        "cluster_adjusted_races": [23438, 39063],
        "years": [6.9, 11.5],
    },
}

# Fitted values above 1 would amplify favorite concentration instead of applying
# the preregistered discount.  The lower bound is the existing 049 search bound.
OPERATIONAL_LAMBDA_ENVELOPE: dict[str, dict[str, float]] = {
    "lambda2": {"min": 0.1, "max": 1.0},
    "lambda3": {"min": 0.1, "max": 1.0},
}

ELIGIBILITY_PREDICATE: dict[str, Any] = {
    "version": "chaos_eligibility_v1",
    "canonical_field": "entry_status=started",
    "minimum_field_size": 4,
    "all_started_horses_require_positive_finite_odds": True,
    "all_started_horses_require_popularity": True,
    "popularity_must_be_unique": True,
    "popularity_gaps_are_allowed": True,
    "implicit_reranking_is_forbidden": True,
}

_EVENT_PREDICATES = {
    "s_ge_20": "ra + rb + rc >= 20",
    "himo_are": "ra <= 3 and (rb >= 10 or rc >= 10)",
    "total_collapse": "ra >= 10",
    "s_ge_30": "ra + rb + rc >= 30",
}


class ChaosArtifactError(ValueError):
    """Base class for typed Feature 084 fit/publish failures."""


class InvalidValidityWindowError(ChaosArtifactError):
    """The confirmation window overlaps the fit window."""


class ArtifactAlreadyExistsError(ChaosArtifactError):
    """The content-addressed target already exists and was not overwritten."""


class ArtifactDigestError(ChaosArtifactError):
    """A supplied artifact digest does not match the canonical payload."""


class NumericStabilityError(ChaosArtifactError):
    """The preregistered representative/degenerate stability gate was not green."""


class OperationalLambdaError(ChaosArtifactError):
    """A fitted lambda lies outside the artifact's operational envelope."""


class ArtifactFitError(ChaosArtifactError):
    """The fit population cannot produce a valid frozen artifact."""


@dataclass(frozen=True)
class ChaosFitHorse:
    """One started horse in the closing-history fit input."""

    horse_id: str
    odds: float | None
    popularity: int | None


@dataclass(frozen=True)
class ChaosFitRace:
    """One race plus exact finish-position labels used only for lambda fitting."""

    race_id: str
    race_date: datetime.date
    horses: tuple[ChaosFitHorse, ...]
    first: tuple[str, ...] = ()
    second: tuple[str, ...] = ()
    third: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishedChaosArtifact:
    """Published payload, its content-addressed path, and fit exclusions."""

    payload: dict[str, Any]
    path: Path
    excluded_race_counts: dict[str, int]

    @property
    def artifact_digest(self) -> str:
        return str(self.payload["artifact_digest"])


@dataclass(frozen=True)
class ChaosDiagnosticRow:
    """One outcome-eligible closing-history race in the secondary OOS report."""

    source: ChaosFitRace
    race_id: str
    day: str
    field_size: int
    band: str
    s: int
    normalized_entropy: float
    expected_s: float
    probabilities: dict[str, float]
    outcomes: dict[str, int]


@dataclass(frozen=True)
class ChaosCoverageRace:
    """One scheduled race and its optional active frozen snapshot."""

    race_id: str
    race_date: datetime.date
    field_size: int
    venue_code: str | None
    track_type: str | None
    grade: str | None
    race_class: str | None
    distance: int | None
    post_time_known: bool
    active_snapshot_count: int
    capture_strength: str | None
    seconds_to_post: int | None

    @property
    def captured(self) -> bool:
        return self.active_snapshot_count > 0


@dataclass(frozen=True)
class FrozenChaosHorse:
    """One horse exactly as stored in ``chaos_snapshots.field``."""

    horse_id: str
    popularity: int
    odds: float


@dataclass(frozen=True)
class ProspectiveChaosRace:
    """One persisted readout joined only to its frozen snapshot and result."""

    race_id: str
    race_date: datetime.date | None
    readout_id: str
    snapshot_id: str
    snapshot_status: str
    capture_strength: str
    seconds_to_post: int | None
    frozen_field: tuple[FrozenChaosHorse, ...]
    field_size: int
    p_s_ge_20: float
    p_himo_are: float
    p_total_collapse: float
    first: tuple[str, ...]
    second: tuple[str, ...]
    third: tuple[str, ...]


@dataclass(frozen=True)
class ProspectiveAnalysisRow:
    """One confirmation-eligible race after frozen-rank outcome derivation."""

    race_id: str
    day: str
    field_size: int
    capture_horizon: str
    probabilities: dict[str, float]
    outcomes: dict[str, int]
    s: int


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def compute_artifact_digest(payload: Mapping[str, Any]) -> str:
    """Canonical SHA-256 over the entire payload except its self-reference."""

    without_self_reference = {
        key: value for key, value in payload.items() if key != "artifact_digest"
    }
    return _stable_hash(without_self_reference)


def _as_iso_date(value: str | datetime.date, *, field: str) -> str:
    if isinstance(value, datetime.datetime):
        raise ChaosArtifactError(f"{field} must be a date, not a datetime")
    if isinstance(value, datetime.date):
        return value.isoformat()
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ChaosArtifactError(f"{field} must be an ISO date") from exc


def _validate_validity_window(
    *, fit_through: str | datetime.date, valid_from: str | datetime.date
) -> None:
    fit_date = datetime.date.fromisoformat(_as_iso_date(fit_through, field="fit_through"))
    valid_date = datetime.date.fromisoformat(_as_iso_date(valid_from, field="valid_from"))
    if valid_date <= fit_date:
        raise InvalidValidityWindowError(
            f"valid_from ({valid_date}) must be after fit_through ({fit_date})"
        )


def _validate_lambda_envelope(
    stage_discount: StageDiscount,
    envelope: Mapping[str, Mapping[str, float]],
) -> None:
    for field in ("lambda2", "lambda3"):
        try:
            bounds = envelope[field]
            lower = float(bounds["min"])
            upper = float(bounds["max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationalLambdaError(
                f"operational envelope for {field} must have numeric min/max"
            ) from exc
        value = float(getattr(stage_discount, field))
        if (
            not math.isfinite(lower)
            or not math.isfinite(upper)
            or lower > upper
            or not lower <= value <= upper
        ):
            raise OperationalLambdaError(
                f"{field}={value} is outside operational envelope [{lower}, {upper}]"
            )


def eligibility_exclusion_reason(race: ChaosFitRace) -> str | None:
    """Apply the single FR-029a predicate used by the closing-history fit."""

    if not race.horses:
        return "no_started_horses"
    if len(race.horses) < int(ELIGIBILITY_PREDICATE["minimum_field_size"]):
        return "field_too_small"

    ranks = [horse.popularity for horse in race.horses]
    if any(
        rank is None or isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0
        for rank in ranks
    ):
        return "invalid_popularity_ranks"
    if len(set(ranks)) != len(ranks):
        return "invalid_popularity_ranks"

    for horse in race.horses:
        if horse.odds is None:
            return "partial_market_odds"
        try:
            odds = float(horse.odds)
        except (TypeError, ValueError):
            return "partial_market_odds"
        if not math.isfinite(odds) or odds <= 0.0:
            return "partial_market_odds"
    return None


def partition_eligible_races(
    races: Sequence[ChaosFitRace],
) -> tuple[list[ChaosFitRace], dict[str, int]]:
    """Return eligible races and deterministic exclusion counts."""

    eligible: list[ChaosFitRace] = []
    counts: Counter[str] = Counter()
    for race in races:
        reason = eligibility_exclusion_reason(race)
        if reason is None:
            eligible.append(race)
        else:
            counts[reason] += 1
    return eligible, dict(sorted(counts.items()))


def load_fit_races(
    session,
    *,
    fit_from: datetime.date,
    fit_to: datetime.date,
) -> list[ChaosFitRace]:
    """Bulk-load every race, its canonical started field, and exact top-3 labels."""

    from horseracing_db.enums import EntryStatus, ResultStatus
    from horseracing_db.models import Race, RaceHorse, RaceResult
    from sqlalchemy import select

    race_rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= fit_from)
        .where(Race.race_date <= fit_to)
        .order_by(Race.race_date, Race.race_id)
    ).all()

    horses_by_race: dict[str, list[ChaosFitHorse]] = defaultdict(list)
    horse_rows = session.execute(
        select(
            RaceHorse.race_id,
            RaceHorse.horse_id,
            RaceHorse.odds,
            RaceHorse.popularity,
        )
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= fit_from)
        .where(Race.race_date <= fit_to)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .order_by(Race.race_date, Race.race_id, RaceHorse.horse_number, RaceHorse.horse_id)
    )
    for row in horse_rows:
        horses_by_race[row.race_id].append(
            ChaosFitHorse(
                horse_id=str(row.horse_id),
                odds=float(row.odds) if row.odds is not None else None,
                popularity=row.popularity,
            )
        )

    finishers_by_race: dict[str, dict[int, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    result_rows = session.execute(
        select(RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order)
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(Race.race_date >= fit_from)
        .where(Race.race_date <= fit_to)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .where(RaceResult.finish_order.in_((1, 2, 3)))
        .order_by(Race.race_date, Race.race_id, RaceResult.finish_order, RaceResult.horse_id)
    )
    for row in result_rows:
        finishers_by_race[row.race_id][int(row.finish_order)].append(str(row.horse_id))

    races: list[ChaosFitRace] = []
    for race_id, race_date in race_rows:
        if race_date is None:
            continue
        finishers = finishers_by_race.get(race_id, {})
        races.append(
            ChaosFitRace(
                race_id=str(race_id),
                race_date=race_date,
                horses=tuple(horses_by_race.get(race_id, ())),
                first=tuple(finishers.get(1, ())),
                second=tuple(finishers.get(2, ())),
                third=tuple(finishers.get(3, ())),
            )
        )
    return races


def _market_inputs(
    race: ChaosFitRace,
) -> tuple[dict[str, float], dict[str, int]]:
    odds = {horse.horse_id: float(horse.odds) for horse in race.horses}
    q = market_implied_win_probs(odds)
    ranks = {horse.horse_id: int(horse.popularity) for horse in race.horses}
    return q, ranks


def _lambda_sample(race: ChaosFitRace, q: Mapping[str, float]) -> MarketLambdaSample:
    horse_ids = tuple(horse.horse_id for horse in race.horses)
    index_by_horse = {horse_id: index for index, horse_id in enumerate(horse_ids)}

    def _unique_index(horse_ids_at_position: tuple[str, ...]) -> int | None:
        if len(horse_ids_at_position) != 1:
            return None
        return index_by_horse.get(horse_ids_at_position[0])

    return MarketLambdaSample(
        win=tuple(float(q[horse_id]) for horse_id in horse_ids),
        i1=_unique_index(race.first),
        i2=_unique_index(race.second),
        i3=_unique_index(race.third),
    )


def _linear_quantile(sorted_values: Sequence[float], probability: float) -> float:
    index = probability * (len(sorted_values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = index - lower
    return float(
        sorted_values[lower]
        + (sorted_values[upper] - sorted_values[lower]) * fraction
    )


def build_field_size_reference_quantiles(
    p_by_field_size: Mapping[int, Sequence[float]],
) -> dict[str, dict[str, Any]]:
    """Freeze 5%-spaced quantiles of P(S>=20) for each canonical field size."""

    references: dict[str, dict[str, Any]] = {}
    for field_size in sorted(p_by_field_size):
        values = sorted(float(value) for value in p_by_field_size[field_size])
        if not values:
            continue
        references[str(field_size)] = {
            "n_races": len(values),
            "quantile_levels": list(_REFERENCE_QUANTILE_LEVELS),
            "p_s_ge_20": [
                _linear_quantile(values, level) for level in _REFERENCE_QUANTILE_LEVELS
            ],
        }
    return references


def within_field_size_percentile(
    p_s_ge_20: float,
    field_size: int,
    references: Mapping[str, Mapping[str, Any]],
) -> float | None:
    """Approximate the within-N percentile (0..100) from frozen quantiles."""

    reference = references.get(str(field_size))
    if reference is None:
        return None
    levels = [float(value) for value in reference["quantile_levels"]]
    values = [float(value) for value in reference["p_s_ge_20"]]
    if len(levels) != len(values) or not levels:
        raise ValueError("field-size reference quantiles are malformed")
    if not math.isfinite(p_s_ge_20):
        raise ValueError("p_s_ge_20 must be finite")

    equal = [index for index, value in enumerate(values) if value == p_s_ge_20]
    if equal:
        return 100.0 * (levels[equal[0]] + levels[equal[-1]]) / 2.0
    if p_s_ge_20 < values[0]:
        return 0.0
    if p_s_ge_20 > values[-1]:
        return 100.0

    right = bisect.bisect_right(values, p_s_ge_20)
    left = right - 1
    value_fraction = (p_s_ge_20 - values[left]) / (values[right] - values[left])
    return 100.0 * (levels[left] + value_fraction * (levels[right] - levels[left]))


def _representative_q(field_size: int, *, power: float) -> dict[str, float]:
    weights = [1.0 / ((index + 1) ** power) for index in range(field_size)]
    total = math.fsum(weights)
    return {
        f"h{index + 1:02d}": weight / total for index, weight in enumerate(weights)
    }


def run_numeric_stability_gate(stage_discount: StageDiscount) -> dict[str, Any]:
    """Exercise representative fields and verify degenerate inputs fail closed."""

    representative: list[dict[str, Any]] = []
    for field_size, power in ((4, 0.0), (8, 0.7), (12, 1.0), (18, 1.5)):
        q = _representative_q(field_size, power=power)
        ranks = {horse_id: index + 1 for index, horse_id in enumerate(q)}
        try:
            raw, adjusted, _ = chaos_readout(
                q,
                ranks,
                CHAOS_EVENTS_V1,
                stage_discount=stage_discount,
                edges=_PROVISIONAL_EDGES,
            )
        except (ChaosInvariantError, ValueError, OverflowError) as exc:
            representative.append(
                {
                    "field_size": field_size,
                    "shape": f"power_{power}",
                    "status": "fail",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            representative.append(
                {
                    "field_size": field_size,
                    "shape": f"power_{power}",
                    "status": "pass",
                    "raw_triple_mass_sum": raw.triple_mass_sum,
                    "adjusted_triple_mass_sum": adjusted.triple_mass_sum,
                }
            )

    field_size = 18
    degenerate_q = {
        f"h{index + 1:02d}": 1.0 if index == 0 else 1e-300
        for index in range(field_size)
    }
    degenerate_ranks = {
        horse_id: index + 1 for index, horse_id in enumerate(degenerate_q)
    }
    try:
        chaos_readout(
            degenerate_q,
            degenerate_ranks,
            CHAOS_EVENTS_V1,
            stage_discount=stage_discount,
            edges=_PROVISIONAL_EDGES,
        )
    except ChaosInvariantError as exc:
        degenerate = [
            {
                "field_size": field_size,
                "shape": "one_dominant_rest_1e-300",
                "expected": "ChaosInvariantError",
                "observed": type(exc).__name__,
                "status": "pass",
            }
        ]
    except (ValueError, OverflowError) as exc:
        degenerate = [
            {
                "field_size": field_size,
                "shape": "one_dominant_rest_1e-300",
                "expected": "ChaosInvariantError",
                "observed": type(exc).__name__,
                "status": "fail",
            }
        ]
    else:
        degenerate = [
            {
                "field_size": field_size,
                "shape": "one_dominant_rest_1e-300",
                "expected": "ChaosInvariantError",
                "observed": "no_error",
                "status": "fail",
            }
        ]

    green = all(row["status"] == "pass" for row in representative + degenerate)
    return {
        "green": green,
        "status": "green" if green else "red",
        "invariant_tolerance": 1e-9,
        "representative_fields": representative,
        "degenerate_fields": degenerate,
    }


def _event_payload(event: EventDefinition) -> dict[str, Any]:
    return {
        "key": event.key,
        "label_ja": event.label_ja,
        "predicate": _EVENT_PREDICATES[event.key],
        "infeasible_when_n_le": event.infeasible_when_n_le,
        "nested_under": event.nested_under,
        "lambda_sensitive": event.lambda_sensitive,
        "promotion_role": event.promotion_role,
        "min_positives_for_decision": event.min_positives_for_decision,
    }


def _fit_input_payload(race: ChaosFitRace) -> dict[str, Any]:
    return {
        "race_id": race.race_id,
        "race_date": race.race_date.isoformat(),
        "field": [
            {
                "horse_id": horse.horse_id,
                "odds": horse.odds,
                "popularity": horse.popularity,
            }
            for horse in race.horses
        ],
        "finishers": {
            "first": list(race.first),
            "second": list(race.second),
            "third": list(race.third),
        },
    }


def _validate_quintile_edges(edges: Sequence[float]) -> None:
    if (
        len(edges) != 4
        or any(not math.isfinite(float(edge)) for edge in edges)
        or any(float(left) >= float(right) for left, right in pairwise(edges))
    ):
        raise ArtifactFitError("quintile_edges must contain four strictly increasing values")


def _validate_publish_payload(payload: Mapping[str, Any]) -> None:
    try:
        _validate_validity_window(
            fit_through=payload["fit_through"],
            valid_from=payload["valid_from"],
        )
        _validate_quintile_edges(payload["quintile_edges"])
        stage_discount = StageDiscount(
            lambda2=float(payload["lambda2"]),
            lambda3=float(payload["lambda3"]),
        )
        envelope = payload["operational_lambda_envelope"]
        report = payload["numeric_stability_report"]
    except KeyError as exc:
        raise ChaosArtifactError(f"missing required artifact field {exc.args[0]!r}") from exc
    _validate_lambda_envelope(stage_discount, envelope)
    if report.get("green") is not True or report.get("status") != "green":
        raise NumericStabilityError("numeric_stability_report is not green")


def publish_artifact(
    payload: Mapping[str, Any],
    *,
    out_dir: str | Path,
) -> PublishedChaosArtifact:
    """Validate, digest, and create ``{digest}.json`` with ``O_EXCL``."""

    _validate_publish_payload(payload)
    body = dict(payload)
    computed_digest = compute_artifact_digest(body)
    supplied_digest = body.get("artifact_digest")
    if supplied_digest is not None and supplied_digest != computed_digest:
        raise ArtifactDigestError(
            f"artifact_digest mismatch: supplied={supplied_digest}, computed={computed_digest}"
        )
    body["artifact_digest"] = computed_digest

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{computed_digest}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise ArtifactAlreadyExistsError(
            f"artifact already exists and will not be overwritten: {path}"
        ) from exc

    serialized = json.dumps(
        body,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as artifact_file:
            artifact_file.write(serialized)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    return PublishedChaosArtifact(
        payload=body,
        path=path,
        excluded_race_counts=dict(body.get("excluded_race_counts", {})),
    )


def fit_artifact(
    session,
    *,
    fit_from: datetime.date,
    fit_to: datetime.date,
    valid_from: datetime.date,
    out_dir: str | Path,
    code_sha: str | None = None,
    min_races: int = DEFAULT_MIN_RACES,
    final_decision_date: datetime.date = DEFAULT_FINAL_DECISION_DATE,
    operational_lambda_envelope: Mapping[str, Mapping[str, float]] | None = None,
) -> PublishedChaosArtifact:
    """Fit market lambdas and P(S>=20) quintiles, then publish one artifact."""

    if fit_from > fit_to:
        raise InvalidValidityWindowError(
            f"fit_from ({fit_from}) must be on or before fit_to ({fit_to})"
        )
    _validate_validity_window(fit_through=fit_to, valid_from=valid_from)

    all_races = load_fit_races(session, fit_from=fit_from, fit_to=fit_to)
    eligible_races, excluded_counts = partition_eligible_races(all_races)
    if len(eligible_races) < 5:
        raise ArtifactFitError(
            f"need at least five eligible races to fit quintiles, got {len(eligible_races)}"
        )

    market_inputs = [_market_inputs(race) for race in eligible_races]
    samples = [
        _lambda_sample(race, q)
        for race, (q, _ranks) in zip(eligible_races, market_inputs, strict=True)
    ]
    stage_discount = fit_chaos_lambda(samples, min_races=min_races)
    envelope = {
        key: {"min": float(bounds["min"]), "max": float(bounds["max"])}
        for key, bounds in (
            operational_lambda_envelope or OPERATIONAL_LAMBDA_ENVELOPE
        ).items()
    }
    _validate_lambda_envelope(stage_discount, envelope)

    probabilities: list[float] = []
    p_by_field_size: dict[int, list[float]] = defaultdict(list)
    for race, (q, ranks) in zip(eligible_races, market_inputs, strict=True):
        try:
            _raw, adjusted, _band = chaos_readout(
                q,
                ranks,
                CHAOS_EVENTS_V1,
                stage_discount=stage_discount,
                edges=_PROVISIONAL_EDGES,
            )
        except ChaosInvariantError as exc:
            raise ArtifactFitError(
                f"eligible race {race.race_id} failed the chaos invariant gate"
            ) from exc
        probability = adjusted.event_mass["s_ge_20"]
        probabilities.append(probability)
        p_by_field_size[len(race.horses)].append(probability)

    quintile_edges = fit_quintile_edges(probabilities)
    _validate_quintile_edges(quintile_edges)
    numeric_stability_report = run_numeric_stability_gate(stage_discount)
    if numeric_stability_report.get("green") is not True:
        raise NumericStabilityError(
            f"numeric stability gate failed: {numeric_stability_report!r}"
        )

    race_ids = sorted(race.race_id for race in eligible_races)
    fit_inputs = [
        _fit_input_payload(race)
        for race in sorted(eligible_races, key=lambda item: item.race_id)
    ]
    payload: dict[str, Any] = {
        "version": ARTIFACT_VERSION,
        "label_definition": LABEL_DEFINITION,
        "lambda2": stage_discount.lambda2,
        "lambda3": stage_discount.lambda3,
        "lambda_fit_objective": {
            "lambda2": "conditional_nll_stage2",
            "lambda3": "conditional_nll_stage3",
        },
        "lambda_fit_counts": {
            "stage2": stage_discount.n_races_l2,
            "stage3": stage_discount.n_races_l3,
            "fallback": stage_discount.fallback,
        },
        "band_axis": BAND_AXIS,
        "band_ids": ["t3_calm", "t3_mild", "t3_mid", "t3_rough", "t3_wild"],
        "quintile_edges": quintile_edges,
        "edge_inclusion_rule": "p <= edge -> lower_band",
        "edges_basis": "closing_history",
        "s_threshold_basis": "fit_window_p90",
        "fit_from": fit_from.isoformat(),
        "fit_to": fit_to.isoformat(),
        "as_of": fit_to.isoformat(),
        "fit_through": fit_to.isoformat(),
        "valid_from": valid_from.isoformat(),
        "n_races_fit": len(eligible_races),
        "race_set_hash": _stable_hash(race_ids),
        "fit_input_hash": _stable_hash(fit_inputs),
        "preregistration": {
            "events": [_event_payload(event) for event in CHAOS_EVENTS_V1],
            "rank_basis": "frozen_explicit_popularity",
            "tie_break_rule": "none; duplicate popularity is excluded; gaps are allowed",
            "exclusion_rules": ELIGIBILITY_PREDICATE,
            "promotion_rule": {
                "controlling_event": "s_ge_20",
                "secondary_event": "himo_are",
                "not_eligible": ["total_collapse"],
                "diagnostic_only": ["s_ge_30"],
                "insufficient_evidence_decision": "NO_DECISION_and_remove_primary_panel",
            },
            "minimum_positives": 100,
            "minimum_race_days": 60,
            "final_decision_date": final_decision_date.isoformat(),
        },
        "numeric_stability_report": numeric_stability_report,
        "operational_lambda_envelope": envelope,
        "eligibility_predicate": ELIGIBILITY_PREDICATE,
        "field_size_reference_quantiles": build_field_size_reference_quantiles(
            p_by_field_size
        ),
        "excluded_race_counts": excluded_counts,
        "code_sha": code_sha or "unknown",
        "calibration_status": "provisional",
    }
    return publish_artifact(payload, out_dir=out_dir)


def _outcome_ranks(
    race: ChaosFitRace,
    ranks: Mapping[str, int],
) -> tuple[tuple[int, int, int] | None, str | None]:
    positions = (race.first, race.second, race.third)
    if any(len(position) > 1 for position in positions):
        return None, "dead_heat"
    if any(len(position) == 0 for position in positions):
        return None, "fewer_than_three_finishers"
    horse_ids = tuple(position[0] for position in positions)
    if len(set(horse_ids)) != 3 or any(horse_id not in ranks for horse_id in horse_ids):
        return None, "partial_ingest"
    return tuple(ranks[horse_id] for horse_id in horse_ids), None


def _cluster_mean_ci(
    days: Sequence[str],
    values: Sequence[float],
    *,
    seed: int,
    b: int,
) -> dict[str, Any]:
    values_by_day: dict[str, list[float]] = defaultdict(list)
    for day, value in zip(days, values, strict=True):
        values_by_day[day].append(float(value))
    ci = race_day_cluster_bootstrap_ci_v1(values_by_day, b=b, seed=seed)
    return {
        "point": None if math.isnan(ci.point) else ci.point,
        "ci_low": ci.ci_low,
        "ci_high": ci.ci_high,
        "n_days": ci.n_days,
        "no_decision": ci.no_decision,
        "b": ci.b,
        "seed": ci.seed,
        "cluster": ci.block,
        "ci_note": DIAGNOSTIC_CI_NOTE,
    }


def _reliability_report(
    days: Sequence[str],
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    seed: int,
    b: int,
) -> dict[str, Any]:
    probability_list = [float(value) for value in probabilities]
    outcome_list = [int(value) for value in outcomes]
    bins = reliability_bins(probability_list, outcome_list)
    reported_bins: list[dict[str, Any]] = []
    ece = 0.0
    for bin_row in bins:
        lo = float(bin_row["pred_lo"])
        hi = float(bin_row["pred_hi"])
        last = hi == 1.0
        indices = [
            index
            for index, probability in enumerate(probability_list)
            if lo <= probability < hi or (last and probability == 1.0)
        ]
        calibration_deltas = [
            probability_list[index] - outcome_list[index] for index in indices
        ]
        calibration_ci = _cluster_mean_ci(
            [days[index] for index in indices],
            calibration_deltas,
            seed=seed,
            b=b,
        )
        ece += (
            len(indices)
            / len(probability_list)
            * abs(float(bin_row["pred_mean"]) - float(bin_row["realized_rate"]))
        )
        reported_bins.append(
            {
                "pred_lo": lo,
                "pred_hi": hi,
                "n": int(bin_row["count"]),
                "predicted_rate": float(bin_row["pred_mean"]),
                "realized_rate": float(bin_row["realized_rate"]),
                "suppressed": bool(bin_row["suppressed"]),
                "predicted_minus_realized_ci": calibration_ci,
            }
        )
    overall_delta = [
        probability - outcome
        for probability, outcome in zip(probability_list, outcome_list, strict=True)
    ]
    return {
        "n": len(probability_list),
        "ece": ece,
        "calibration_in_the_large": math.fsum(overall_delta) / len(overall_delta),
        "predicted_minus_realized_ci": _cluster_mean_ci(
            days,
            overall_delta,
            seed=seed,
            b=b,
        ),
        "bins": reported_bins,
        "ci_note": DIAGNOSTIC_CI_NOTE,
    }


def _decision_status(event: EventDefinition, positives: int) -> tuple[str, str]:
    minimum = event.min_positives_for_decision
    if event.promotion_role in {"diagnostic_only", "not_eligible"} or minimum is None:
        return (
            "NO_DECISION",
            f"event promotion_role={event.promotion_role}; this diagnostic cannot adopt",
        )
    if positives < minimum:
        return (
            "NO_DECISION",
            f"positives {positives} < min_positives_for_decision {minimum}",
        )
    return (
        "SECONDARY_REPORT_ONLY",
        "minimum count met, but this command is not an adoption gate",
    )


def _score_report(
    days: Sequence[str],
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    event: EventDefinition,
    *,
    seed: int,
    b: int,
) -> dict[str, Any]:
    probability_array = np.asarray(probabilities, dtype=float)
    outcome_array = np.asarray(outcomes, dtype=int)
    clipped = np.clip(probability_array, _SCORE_CLIP, 1.0 - _SCORE_CLIP)
    brier_losses = (probability_array - outcome_array) ** 2
    log_losses = -(
        outcome_array * np.log(clipped)
        + (1 - outcome_array) * np.log(1.0 - clipped)
    )
    positives = int(outcome_array.sum())
    decision_status, decision_reason = _decision_status(event, positives)
    return {
        "n": len(outcome_array),
        "positives": positives,
        "predicted_rate": float(probability_array.mean()),
        "realized_rate": float(outcome_array.mean()),
        "reliability": _reliability_report(
            days,
            probability_array,
            outcome_array,
            seed=seed,
            b=b,
        ),
        "brier": {
            "point": brier_label(probability_array, outcome_array),
            "cluster_ci": _cluster_mean_ci(
                days,
                brier_losses,
                seed=seed,
                b=b,
            ),
            "proper_score": True,
        },
        "log_score": {
            "point": log_loss_label(probability_array, outcome_array),
            "cluster_ci": _cluster_mean_ci(
                days,
                log_losses,
                seed=seed,
                b=b,
            ),
            "proper_score": True,
        },
        "auc": {
            "point": auc_label(probability_array, outcome_array),
            "role": "AUXILIARY_ONLY",
            "decision_forbidden": True,
        },
        "decision_status": decision_status,
        "decision_reason": decision_reason,
        "min_positives_for_decision": event.min_positives_for_decision,
        "promotion_role": event.promotion_role,
    }


def _empty_score_report(event: EventDefinition) -> dict[str, Any]:
    return {
        "n": 0,
        "positives": 0,
        "predicted_rate": None,
        "realized_rate": None,
        "reliability": None,
        "brier": None,
        "log_score": None,
        "auc": {
            "point": None,
            "role": "AUXILIARY_ONLY",
            "decision_forbidden": True,
        },
        "decision_status": "NO_DECISION",
        "decision_reason": "no races in band or bucket",
        "min_positives_for_decision": event.min_positives_for_decision,
        "promotion_role": event.promotion_role,
    }


def _fit_logistic_probabilities(
    features: np.ndarray,
    outcomes: Sequence[int],
) -> np.ndarray:
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    outcome_array = np.asarray(outcomes, dtype=int)
    if len(np.unique(outcome_array)) < 2:
        constant = float(np.clip(outcome_array.mean(), 1e-6, 1.0 - 1e-6))
        return np.full(len(outcome_array), constant, dtype=float)
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=1e6,
            max_iter=1000,
            random_state=DIAGNOSTIC_BOOTSTRAP_SEED,
            solver="lbfgs",
        ),
    )
    model.fit(features, outcome_array)
    return np.clip(model.predict_proba(features)[:, 1], 1e-6, 1.0 - 1e-6)


def _baseline_probabilities(
    rows: Sequence[ChaosDiagnosticRow],
    event: EventDefinition,
) -> dict[str, np.ndarray]:
    outcomes = [row.outcomes[event.key] for row in rows]
    field_sizes = np.asarray([[row.field_size] for row in rows], dtype=float)
    entropy_and_n = np.asarray(
        [[row.normalized_entropy, row.field_size] for row in rows],
        dtype=float,
    )
    return {
        "n_only": _fit_logistic_probabilities(field_sizes, outcomes),
        "g_h_n": _fit_logistic_probabilities(entropy_and_n, outcomes),
    }


def _proper_score_points(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
) -> dict[str, Any]:
    return {
        "brier": brier_label(probabilities, outcomes),
        "log_score": log_loss_label(probabilities, outcomes),
        "auc_auxiliary": auc_label(probabilities, outcomes),
    }


def _paired_loss_deltas(
    days: Sequence[str],
    candidate: Sequence[float],
    baseline: Sequence[float],
    outcomes: Sequence[int],
    *,
    seed: int,
    b: int,
) -> dict[str, Any]:
    candidate_array = np.asarray(candidate, dtype=float)
    baseline_array = np.asarray(baseline, dtype=float)
    outcome_array = np.asarray(outcomes, dtype=int)
    candidate_clipped = np.clip(candidate_array, _SCORE_CLIP, 1.0 - _SCORE_CLIP)
    baseline_clipped = np.clip(baseline_array, _SCORE_CLIP, 1.0 - _SCORE_CLIP)
    brier_delta = (
        (candidate_array - outcome_array) ** 2
        - (baseline_array - outcome_array) ** 2
    )
    candidate_log = -(
        outcome_array * np.log(candidate_clipped)
        + (1 - outcome_array) * np.log(1.0 - candidate_clipped)
    )
    baseline_log = -(
        outcome_array * np.log(baseline_clipped)
        + (1 - outcome_array) * np.log(1.0 - baseline_clipped)
    )
    return {
        "direction": "chaos_minus_baseline; negative favors chaos",
        "brier_delta": _cluster_mean_ci(days, brier_delta, seed=seed, b=b),
        "log_score_delta": _cluster_mean_ci(
            days,
            candidate_log - baseline_log,
            seed=seed,
            b=b,
        ),
        "ci_note": DIAGNOSTIC_CI_NOTE,
    }


def _field_size_bucket(field_size: int) -> str:
    for name, lower, upper in _FIELD_SIZE_BUCKETS:
        if field_size >= lower and (upper is None or field_size <= upper):
            return name
    raise ValueError(f"field size {field_size} is outside diagnostic buckets")


def _within_field_size_report(
    rows: Sequence[ChaosDiagnosticRow],
    baselines: Mapping[str, Mapping[str, np.ndarray]],
    *,
    seed: int,
    b: int,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for bucket_name, _lower, _upper in _FIELD_SIZE_BUCKETS:
        indices = [
            index
            for index, row in enumerate(rows)
            if _field_size_bucket(row.field_size) == bucket_name
        ]
        if not indices:
            continue
        bucket_rows = [rows[index] for index in indices]
        days = [row.day for row in bucket_rows]
        event_reports: dict[str, Any] = {}
        for event in CHAOS_EVENTS_V1:
            outcomes = [row.outcomes[event.key] for row in bucket_rows]
            chaos_probabilities = [
                row.probabilities[event.key] for row in bucket_rows
            ]
            n_only = baselines[event.key]["n_only"][indices]
            g_h_n = baselines[event.key]["g_h_n"][indices]
            positives = sum(outcomes)
            decision_status, decision_reason = _decision_status(event, positives)
            entropy_auc = auc_label(
                [row.normalized_entropy for row in bucket_rows],
                outcomes,
            )
            expected_s_auc = auc_label(
                [row.expected_s for row in bucket_rows],
                outcomes,
            )
            event_reports[event.key] = {
                "n": len(bucket_rows),
                "positives": positives,
                "proper_scores": {
                    "chaos_probability": _proper_score_points(
                        chaos_probabilities,
                        outcomes,
                    ),
                    "n_only": _proper_score_points(n_only, outcomes),
                    "g_h_n": _proper_score_points(g_h_n, outcomes),
                },
                "auxiliary_auc_scores": {
                    "normalized_entropy_h": entropy_auc,
                    "expected_s": expected_s_auc,
                },
                "paired_deltas": {
                    "chaos_minus_n_only": _paired_loss_deltas(
                        days,
                        chaos_probabilities,
                        n_only,
                        outcomes,
                        seed=seed,
                        b=b,
                    ),
                    "chaos_minus_g_h_n": _paired_loss_deltas(
                        days,
                        chaos_probabilities,
                        g_h_n,
                        outcomes,
                        seed=seed,
                        b=b,
                    ),
                },
                "auc_role": "AUXILIARY_ONLY; never decide on AUC alone",
                "decision_status": decision_status,
                "decision_reason": decision_reason,
            }
        reports.append(
            {
                "bucket": bucket_name,
                "n": len(bucket_rows),
                "mean_field_size": math.fsum(row.field_size for row in bucket_rows)
                / len(bucket_rows),
                "events": event_reports,
            }
        )
    return reports


def export_chaos_diagnostic_fixture(
    rows: Sequence[ChaosDiagnosticRow],
    path: str | Path,
) -> dict[str, Any]:
    """Write the SC-008 closing-history fixture and a SHA-256 sidecar."""

    import pandas as pd

    fixture_path = Path(path)
    if fixture_path.suffix != ".parquet":
        raise ValueError("--export-fixture path must end in .parquet")
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item.race_id):
        race = row.source
        records.append(
            {
                "race_id": race.race_id,
                "race_date": race.race_date.isoformat(),
                "horse_ids": [horse.horse_id for horse in race.horses],
                "popularity": [int(horse.popularity) for horse in race.horses],
                "odds": [float(horse.odds) for horse in race.horses],
                "first_horse_id": race.first[0],
                "second_horse_id": race.second[0],
                "third_horse_id": race.third[0],
            }
        )
    pd.DataFrame.from_records(records).to_parquet(fixture_path, index=False)
    digest = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    digest_path = Path(f"{fixture_path}.sha256")
    digest_path.write_text(f"{digest}  {fixture_path.name}\n", encoding="utf-8")
    return {
        "path": str(fixture_path),
        "sha256": digest,
        "sha256_path": str(digest_path),
        "n_races": len(records),
        "columns": [
            "race_id",
            "race_date",
            "horse_ids",
            "popularity",
            "odds",
            "first_horse_id",
            "second_horse_id",
            "third_horse_id",
        ],
        "outcome_columns_note": "only first/second/third horse ids are exported",
    }


def load_coverage_rows(
    session,
    *,
    report_from: datetime.date,
    report_to: datetime.date,
) -> list[ChaosCoverageRace]:
    """Load the US6 denominator and its optional active snapshot without result filters."""

    from horseracing_db.enums import EntryStatus
    from horseracing_db.models import ChaosSnapshot, Race, RaceHorse
    from sqlalchemy import select

    race_rows = session.execute(
        select(
            Race.race_id,
            Race.race_date,
            Race.venue_code,
            Race.track_type,
            Race.grade,
            Race.race_class,
            Race.distance,
            Race.post_time,
        )
        .where(Race.race_date >= report_from)
        .where(Race.race_date <= report_to)
        .order_by(Race.race_date, Race.race_id)
    ).all()

    field_sizes: Counter[str] = Counter()
    for race_id, _horse_id in session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_id)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= report_from)
        .where(Race.race_date <= report_to)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ):
        field_sizes[str(race_id)] += 1

    snapshots_by_race: dict[str, list[Any]] = defaultdict(list)
    snapshot_rows = session.execute(
        select(
            ChaosSnapshot.race_id,
            ChaosSnapshot.chaos_snapshot_id,
            ChaosSnapshot.capture_strength,
            ChaosSnapshot.seconds_to_post,
            ChaosSnapshot.captured_at,
        )
        .join(Race, Race.race_id == ChaosSnapshot.race_id)
        .where(Race.race_date >= report_from)
        .where(Race.race_date <= report_to)
        .where(ChaosSnapshot.status == "active")
        .order_by(ChaosSnapshot.race_id, ChaosSnapshot.captured_at)
    ).all()
    for row in snapshot_rows:
        snapshots_by_race[str(row.race_id)].append(row)

    rows: list[ChaosCoverageRace] = []
    for row in race_rows:
        if row.race_date is None:
            continue
        snapshots = snapshots_by_race.get(str(row.race_id), [])
        snapshot = snapshots[0] if len(snapshots) == 1 else None
        rows.append(
            ChaosCoverageRace(
                race_id=str(row.race_id),
                race_date=row.race_date,
                field_size=field_sizes[str(row.race_id)],
                venue_code=row.venue_code,
                track_type=row.track_type,
                grade=row.grade,
                race_class=row.race_class,
                distance=row.distance,
                post_time_known=row.post_time is not None,
                active_snapshot_count=len(snapshots),
                capture_strength=(
                    str(snapshot.capture_strength)
                    if snapshot is not None
                    else (
                        "invalid_multiple_active"
                        if len(snapshots) > 1
                        else None
                    )
                ),
                seconds_to_post=(
                    int(snapshot.seconds_to_post)
                    if snapshot is not None
                    and snapshot.seconds_to_post is not None
                    else None
                ),
            )
        )
    return rows


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _count_summary(values: Sequence[str]) -> dict[str, Any]:
    counts = Counter(values)
    denominator = len(values)
    return {
        "counts": dict(sorted(counts.items())),
        "rates": {
            key: count / denominator for key, count in sorted(counts.items())
        },
    }


def _distribution_summary(values: Sequence[int | float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "n": 0,
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
        }
    array = np.asarray(values, dtype=float)
    return {
        "n": len(array),
        "min": float(array.min()),
        "p10": float(np.quantile(array, 0.10)),
        "p25": float(np.quantile(array, 0.25)),
        "median": float(np.quantile(array, 0.50)),
        "p75": float(np.quantile(array, 0.75)),
        "p90": float(np.quantile(array, 0.90)),
        "max": float(array.max()),
    }


def _coverage_field_size_bucket(field_size: int) -> str:
    if field_size < 4:
        return "0-3"
    return _field_size_bucket(field_size)


def _race_characteristics(rows: Sequence[ChaosCoverageRace]) -> dict[str, Any]:
    if not rows:
        return {
            "n_races": 0,
            "n_race_days": 0,
            "mean_field_size": None,
            "post_time_known_rate": None,
            "field_size_buckets": {"counts": {}, "rates": {}},
            "venues": {"counts": {}, "rates": {}},
            "track_types": {"counts": {}, "rates": {}},
            "grades": {"counts": {}, "rates": {}},
            "race_classes": {"counts": {}, "rates": {}},
            "distance": _distribution_summary([]),
        }

    def value_or_unknown(value: Any) -> str:
        return "unknown" if value is None or value == "" else str(value)

    return {
        "n_races": len(rows),
        "n_race_days": len({row.race_date for row in rows}),
        "mean_field_size": math.fsum(row.field_size for row in rows) / len(rows),
        "post_time_known_rate": _rate(
            sum(row.post_time_known for row in rows),
            len(rows),
        ),
        "field_size_buckets": _count_summary(
            [_coverage_field_size_bucket(row.field_size) for row in rows]
        ),
        "venues": _count_summary(
            [value_or_unknown(row.venue_code) for row in rows]
        ),
        "track_types": _count_summary(
            [value_or_unknown(row.track_type) for row in rows]
        ),
        "grades": _count_summary([value_or_unknown(row.grade) for row in rows]),
        "race_classes": _count_summary(
            [value_or_unknown(row.race_class) for row in rows]
        ),
        "distance": _distribution_summary(
            [row.distance for row in rows if row.distance is not None]
        ),
    }


def coverage_report(
    session,
    *,
    report_from: datetime.date,
    report_to: datetime.date,
) -> dict[str, Any]:
    """Report US6 capture/post-time coverage and uncaptured-race characteristics."""

    if report_to < report_from:
        raise ValueError("report_to must be on or after report_from")
    rows = load_coverage_rows(
        session,
        report_from=report_from,
        report_to=report_to,
    )
    captured = [row for row in rows if row.captured]
    not_captured = [row for row in rows if not row.captured]
    strengths = Counter(
        row.capture_strength or "unknown" for row in captured
    )
    for strength in ("confirmatory", "weak", "unknown"):
        strengths.setdefault(strength, 0)
    numeric_seconds = [
        row.seconds_to_post
        for row in captured
        if row.seconds_to_post is not None
    ]
    seconds_summary = _distribution_summary(numeric_seconds)
    seconds_summary.update(
        {
            "n_numeric": len(numeric_seconds),
            "n_missing": len(captured) - len(numeric_seconds),
            "unit": "seconds",
        }
    )

    years = range(report_from.year, report_to.year + 1)
    post_time_by_year: dict[str, dict[str, Any]] = {}
    for year in years:
        year_rows = [row for row in rows if row.race_date.year == year]
        known = sum(row.post_time_known for row in year_rows)
        post_time_by_year[str(year)] = {
            "numerator": known,
            "denominator": len(year_rows),
            "rate": _rate(known, len(year_rows)),
        }
    post_time_known = sum(row.post_time_known for row in rows)
    strength_denominator = len(captured)
    return {
        "schema_version": "chaos-coverage-v1",
        "window": {
            "from": report_from.isoformat(),
            "to": report_to.isoformat(),
        },
        "population": {
            "scheduled_races": len(rows),
            "race_days": len({row.race_date for row in rows}),
            "captured_races": len(captured),
            "not_captured_races": len(not_captured),
            "multiple_active_snapshot_races": sum(
                row.active_snapshot_count > 1 for row in rows
            ),
        },
        "capture_rate": {
            "numerator": len(captured),
            "denominator": len(rows),
            "rate": _rate(len(captured), len(rows)),
        },
        "capture_strength": {
            "denominator": "captured_races",
            "counts": dict(sorted(strengths.items())),
            "rates": {
                key: _rate(count, strength_denominator)
                for key, count in sorted(strengths.items())
            },
        },
        "seconds_to_post": seconds_summary,
        "post_time_coverage": {
            "numerator": post_time_known,
            "denominator": len(rows),
            "rate": _rate(post_time_known, len(rows)),
            "by_year": post_time_by_year,
            "historical_reference": dict(_POST_TIME_HISTORICAL_REFERENCE),
            "interpretation": (
                "confirmatory capture requires post_time; historical 0%/22.9%/100% "
                "coverage reflects the netkeiba-era source transition and is not a "
                "capture bug"
            ),
        },
        "captured_characteristics": _race_characteristics(captured),
        "not_captured_characteristics": _race_characteristics(not_captured),
        "selection_bias_note": (
            "Compare captured and not_captured characteristics; systematic field-size, "
            "venue, surface, class, distance, or post_time differences indicate selection bias."
        ),
    }


def _parse_frozen_field(value: Any) -> tuple[FrozenChaosHorse, ...]:
    if not isinstance(value, list):
        return ()
    parsed: list[FrozenChaosHorse] = []
    try:
        for item in value:
            if not isinstance(item, Mapping):
                return ()
            popularity = item["popularity"]
            odds = item["odds"]
            horse_id = item["horse_id"]
            if (
                isinstance(popularity, bool)
                or not isinstance(popularity, int)
                or popularity <= 0
                or isinstance(odds, bool)
                or not math.isfinite(float(odds))
                or float(odds) <= 0.0
                or not isinstance(horse_id, str)
                or not horse_id
            ):
                return ()
            parsed.append(
                FrozenChaosHorse(
                    horse_id=horse_id,
                    popularity=popularity,
                    odds=float(odds),
                )
            )
    except (KeyError, TypeError, ValueError):
        return ()
    return tuple(parsed)


def load_prospective_rows(
    session,
    *,
    artifact_digest: str,
    through_date: datetime.date,
) -> list[ProspectiveChaosRace]:
    """Load readouts with frozen fields and finishers; live popularity is never joined."""

    from horseracing_db.enums import ResultStatus
    from horseracing_db.models import ChaosReadout, ChaosSnapshot, Race, RaceResult
    from sqlalchemy import select

    readout_rows = session.execute(
        select(
            ChaosReadout.chaos_readout_id,
            ChaosReadout.chaos_snapshot_id,
            ChaosReadout.p_s_ge_20,
            ChaosReadout.p_himo_are,
            ChaosReadout.p_total_collapse,
            ChaosSnapshot.race_id,
            ChaosSnapshot.status,
            ChaosSnapshot.capture_strength,
            ChaosSnapshot.seconds_to_post,
            ChaosSnapshot.field,
            ChaosSnapshot.n,
            Race.race_date,
        )
        .join(
            ChaosSnapshot,
            ChaosSnapshot.chaos_snapshot_id == ChaosReadout.chaos_snapshot_id,
        )
        .join(Race, Race.race_id == ChaosSnapshot.race_id)
        .where(ChaosReadout.artifact_digest == artifact_digest)
        .where(Race.race_date <= through_date)
        .order_by(Race.race_date, ChaosSnapshot.race_id)
    ).all()
    race_ids = sorted({str(row.race_id) for row in readout_rows})

    finishers: dict[str, dict[int, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    if race_ids:
        result_rows = session.execute(
            select(RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order)
            .where(RaceResult.race_id.in_(race_ids))
            .where(RaceResult.result_status == ResultStatus.FINISHED)
            .where(RaceResult.finish_order.in_((1, 2, 3)))
            .order_by(
                RaceResult.race_id,
                RaceResult.finish_order,
                RaceResult.horse_id,
            )
        )
        for row in result_rows:
            finishers[str(row.race_id)][int(row.finish_order)].append(
                str(row.horse_id)
            )

    rows: list[ProspectiveChaosRace] = []
    for row in readout_rows:
        positions = finishers.get(str(row.race_id), {})
        rows.append(
            ProspectiveChaosRace(
                race_id=str(row.race_id),
                race_date=row.race_date,
                readout_id=str(row.chaos_readout_id),
                snapshot_id=str(row.chaos_snapshot_id),
                snapshot_status=str(row.status),
                capture_strength=str(row.capture_strength),
                seconds_to_post=(
                    int(row.seconds_to_post)
                    if row.seconds_to_post is not None
                    else None
                ),
                frozen_field=_parse_frozen_field(row.field),
                field_size=int(row.n),
                p_s_ge_20=float(row.p_s_ge_20),
                p_himo_are=float(row.p_himo_are),
                p_total_collapse=float(row.p_total_collapse),
                first=tuple(positions.get(1, ())),
                second=tuple(positions.get(2, ())),
                third=tuple(positions.get(3, ())),
            )
        )
    return rows


def _frozen_outcome(
    row: ProspectiveChaosRace,
) -> tuple[tuple[int, dict[str, int]] | None, str | None]:
    field = row.frozen_field
    if (
        len(field) != row.field_size
        or row.field_size < 4
        or len({horse.horse_id for horse in field}) != len(field)
        or len({horse.popularity for horse in field}) != len(field)
    ):
        return None, "invalid_snapshot_field"
    positions = (row.first, row.second, row.third)
    if any(len(position) > 1 for position in positions):
        return None, "dead_heat"
    if any(len(position) == 0 for position in positions):
        return None, "fewer_than_three_finishers"
    finishers = tuple(position[0] for position in positions)
    ranks = {horse.horse_id: horse.popularity for horse in field}
    if len(set(finishers)) != 3 or any(horse_id not in ranks for horse_id in finishers):
        return None, "partial_ingest"
    ra, rb, rc = (ranks[horse_id] for horse_id in finishers)
    outcomes = {
        event.key: int(event.predicate(ra, rb, rc, row.field_size))
        for event in CHAOS_EVENTS_V1
    }
    return (ra + rb + rc, outcomes), None


def _capture_horizon(seconds_to_post: int) -> str:
    for name, lower, upper in _CAPTURE_HORIZON_BUCKETS:
        if seconds_to_post >= lower and (
            upper is None or seconds_to_post <= upper
        ):
            return name
    raise ValueError("confirmatory seconds_to_post must be non-negative")


def _primary_horizon(
    preregistration: Mapping[str, Any],
) -> dict[str, Any]:
    raw = preregistration.get("primary_horizon")
    if not isinstance(raw, Mapping):
        return {
            "mode": "sole_active_confirmatory_snapshot_per_race",
            "minimum_seconds_to_post": 0,
            "maximum_seconds_to_post": None,
            "artifact_field_present": False,
        }
    lower = raw.get(
        "minimum_seconds_to_post",
        raw.get("min_seconds_to_post", 0),
    )
    upper = raw.get(
        "maximum_seconds_to_post",
        raw.get("max_seconds_to_post"),
    )
    try:
        minimum = int(lower)
        maximum = None if upper is None else int(upper)
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact primary_horizon bounds must be integers") from exc
    if minimum < 0 or (maximum is not None and maximum < minimum):
        raise ValueError("artifact primary_horizon bounds are invalid")
    return {
        "mode": "artifact_seconds_to_post_window",
        "minimum_seconds_to_post": minimum,
        "maximum_seconds_to_post": maximum,
        "artifact_field_present": True,
    }


def _within_primary_horizon(
    seconds_to_post: int,
    horizon: Mapping[str, Any],
) -> bool:
    minimum = int(horizon["minimum_seconds_to_post"])
    maximum = horizon["maximum_seconds_to_post"]
    return seconds_to_post >= minimum and (
        maximum is None or seconds_to_post <= int(maximum)
    )


def _s_ge_30_probability(
    row: ProspectiveChaosRace,
    artifact,
) -> float | None:
    try:
        odds = {horse.horse_id: horse.odds for horse in row.frozen_field}
        ranks = {
            horse.horse_id: horse.popularity for horse in row.frozen_field
        }
        q = market_implied_win_probs(odds)
        _raw, adjusted, _band = chaos_readout(
            q,
            ranks,
            CHAOS_EVENTS_V1,
            stage_discount=StageDiscount(
                lambda2=float(artifact.lambda2),
                lambda3=float(artifact.lambda3),
            ),
            edges=artifact.quintile_edges,
        )
    except (ChaosInvariantError, KeyError, TypeError, ValueError):
        return None
    return float(adjusted.event_mass["s_ge_30"])


def _prospective_analysis_rows(
    rows: Sequence[ProspectiveChaosRace],
    *,
    artifact,
    as_of: datetime.date,
) -> tuple[list[ProspectiveAnalysisRow], dict[str, int], dict[str, Any]]:
    valid_from = artifact.valid_from
    if isinstance(valid_from, str):
        valid_from = datetime.date.fromisoformat(valid_from)
    preregistration = artifact.preregistration
    horizon = _primary_horizon(preregistration)
    by_race: dict[str, list[ProspectiveChaosRace]] = defaultdict(list)
    for row in rows:
        by_race[row.race_id].append(row)

    exclusions: Counter[str] = Counter()
    analyzed: list[ProspectiveAnalysisRow] = []
    for race_id in sorted(by_race):
        race_rows = by_race[race_id]
        if len(race_rows) != 1:
            exclusions["not_one_row_per_race"] += 1
            continue
        row = race_rows[0]
        if row.race_date is None:
            exclusions["missing_race_date"] += 1
            continue
        if row.race_date < valid_from:
            exclusions["before_valid_from"] += 1
            continue
        if row.race_date > as_of:
            exclusions["after_report_date"] += 1
            continue
        if row.snapshot_status != "active":
            exclusions["snapshot_not_active"] += 1
            continue
        if row.capture_strength != "confirmatory":
            exclusions["non_confirmatory_capture"] += 1
            continue
        if row.seconds_to_post is None:
            exclusions["confirmatory_missing_seconds_to_post"] += 1
            continue
        if row.seconds_to_post < 0:
            exclusions["captured_at_or_after_post"] += 1
            continue
        if not _within_primary_horizon(row.seconds_to_post, horizon):
            exclusions["outside_primary_horizon"] += 1
            continue
        frozen_outcome, outcome_reason = _frozen_outcome(row)
        if frozen_outcome is None:
            exclusions[outcome_reason or "partial_ingest"] += 1
            continue
        s, outcomes = frozen_outcome
        probabilities = {
            "s_ge_20": row.p_s_ge_20,
            "himo_are": row.p_himo_are,
            "total_collapse": row.p_total_collapse,
        }
        s_ge_30 = _s_ge_30_probability(row, artifact)
        if s_ge_30 is not None:
            probabilities["s_ge_30"] = s_ge_30
        analyzed.append(
            ProspectiveAnalysisRow(
                race_id=row.race_id,
                day=row.race_date.isoformat(),
                field_size=row.field_size,
                capture_horizon=_capture_horizon(row.seconds_to_post),
                probabilities=probabilities,
                outcomes=outcomes,
                s=s,
            )
        )
    return analyzed, dict(sorted(exclusions.items())), horizon


def _prospective_event_report(
    rows: Sequence[ProspectiveAnalysisRow],
    event: EventDefinition,
    *,
    seed: int,
    b: int,
) -> dict[str, Any]:
    scorable = [row for row in rows if event.key in row.probabilities]
    if not scorable:
        report = _empty_score_report(event)
    else:
        report = _score_report(
            [row.day for row in scorable],
            [row.probabilities[event.key] for row in scorable],
            [row.outcomes[event.key] for row in scorable],
            event,
            seed=seed,
            b=b,
        )
    report["decision_status"] = "REPORTED_NOT_DECIDED_HERE"
    report["decision_reason"] = (
        "Only the top-level preregistered p_s_ge_20 promotion gate can decide."
    )
    report["decision_use"] = (
        "CONTROLS_PROMOTION"
        if event.key == "s_ge_20"
        else event.promotion_role.upper()
    )
    return report


def _prospective_metric_group(
    rows: Sequence[ProspectiveAnalysisRow],
    *,
    seed: int,
    b: int,
) -> dict[str, Any]:
    return {
        "n": len(rows),
        "events": {
            event.key: _prospective_event_report(
                rows,
                event,
                seed=seed,
                b=b,
            )
            for event in CHAOS_EVENTS_V1
        },
        "metric_priority": (
            "reliability/Brier/log_score PRIMARY; AUC AUXILIARY_ONLY"
        ),
    }


def _calibration_tolerance(
    preregistration: Mapping[str, Any],
) -> tuple[float | None, Any]:
    raw = preregistration.get("calibration_tolerance")
    if isinstance(raw, Mapping) and isinstance(raw.get("s_ge_20"), Mapping):
        raw = raw["s_ge_20"]
    if isinstance(raw, Mapping):
        for key in (
            "absolute_calibration_error_max",
            "max_absolute_calibration_error",
            "calibration_in_the_large_abs_max",
            "absolute_error_max",
        ):
            if key in raw:
                try:
                    value = float(raw[key])
                except (TypeError, ValueError):
                    return None, raw
                return (value if math.isfinite(value) and value >= 0.0 else None), raw
        return None, raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        return (value if math.isfinite(value) and value >= 0.0 else None), raw
    return None, raw


def _promotion_report(
    rows: Sequence[ProspectiveAnalysisRow],
    overall: Mapping[str, Any],
    *,
    preregistration: Mapping[str, Any],
    as_of: datetime.date,
) -> dict[str, Any]:
    rule = preregistration.get("promotion_rule")
    if not isinstance(rule, Mapping):
        rule = {}
    controlling_event = str(rule.get("controlling_event", "s_ge_20"))
    secondary_event = str(rule.get("secondary_event", "himo_are"))
    not_eligible = [str(value) for value in rule.get("not_eligible", ["total_collapse"])]
    diagnostic_only = [str(value) for value in rule.get("diagnostic_only", ["s_ge_30"])]
    try:
        minimum_positives = int(preregistration["minimum_positives"])
        minimum_race_days = int(preregistration["minimum_race_days"])
        final_decision_date = datetime.date.fromisoformat(
            str(preregistration["final_decision_date"])
        )
    except (KeyError, TypeError, ValueError):
        minimum_positives = 0
        minimum_race_days = 0
        final_decision_date = as_of - datetime.timedelta(days=1)
        preregistration_valid = False
    else:
        preregistration_valid = (
            minimum_positives > 0 and minimum_race_days > 0
        )

    primary = overall["events"].get(controlling_event, {})
    positives = int(primary.get("positives", 0))
    race_days = len({row.day for row in rows})
    past_date = as_of > final_decision_date
    tolerance, raw_tolerance = _calibration_tolerance(preregistration)
    multiplicity_policy = preregistration.get(
        "multiplicity_policy",
        preregistration.get("multiple_comparison_policy"),
    )
    reasons: list[str] = []
    if controlling_event != "s_ge_20":
        reasons.append(
            "artifact controlling_event is not p_s_ge_20; FR-030 forbids promotion"
        )
    if positives < minimum_positives:
        reasons.append(
            f"p_s_ge_20 positives {positives} < minimum {minimum_positives}"
        )
    if race_days < minimum_race_days:
        reasons.append(
            f"race days {race_days} < minimum {minimum_race_days}"
        )
    if past_date:
        reasons.append(
            f"report date {as_of.isoformat()} is past final decision date "
            f"{final_decision_date.isoformat()}"
        )
    if not preregistration_valid:
        reasons.append("minimum sample/date preregistration is missing or invalid")
    if tolerance is None:
        reasons.append("calibration tolerance is not preregistered")
    if multiplicity_policy is None:
        reasons.append("multiplicity policy is not preregistered")

    calibration_passed: bool | None = None
    calibration_ci = None
    reliability = primary.get("reliability")
    if isinstance(reliability, Mapping):
        calibration_ci = reliability.get("predicted_minus_realized_ci")
    if tolerance is not None and isinstance(calibration_ci, Mapping):
        ci_low = calibration_ci.get("ci_low")
        ci_high = calibration_ci.get("ci_high")
        if ci_low is not None and ci_high is not None:
            calibration_passed = (
                float(ci_low) >= -tolerance
                and float(ci_high) <= tolerance
            )

    hard_no_decision = (
        controlling_event != "s_ge_20"
        or positives < minimum_positives
        or race_days < minimum_race_days
        or past_date
        or not preregistration_valid
        or tolerance is None
        or multiplicity_policy is None
        or calibration_passed is None
    )
    if hard_no_decision:
        decision = "NO_DECISION"
    elif calibration_passed:
        decision = "PROMOTE"
    else:
        decision = "DO_NOT_PROMOTE"
        reasons.append("p_s_ge_20 calibration CI is outside the preregistered tolerance")

    remove_panel = decision != "PROMOTE"
    return {
        "decision": decision,
        "decision_reasons": reasons,
        "controlling_event": "s_ge_20",
        "secondary_event": secondary_event,
        "not_eligible": not_eligible,
        "diagnostic_only": diagnostic_only,
        "minimum_positives": minimum_positives,
        "observed_positives": positives,
        "minimum_race_days": minimum_race_days,
        "observed_race_days": race_days,
        "final_decision_date": final_decision_date.isoformat(),
        "report_date": as_of.isoformat(),
        "past_final_decision_date": past_date,
        "calibration_tolerance": raw_tolerance,
        "calibration_tolerance_value": tolerance,
        "calibration_ci": calibration_ci,
        "calibration_passed": calibration_passed,
        "multiplicity_policy": multiplicity_policy,
        "auc_used_for_decision": False,
        "panel_action": (
            "REMOVE_FROM_MAIN_PANEL"
            if remove_panel
            else "ISSUE_NEW_CONFIRMED_ARTIFACT_VERSION"
        ),
        "panel_action_ja": (
            "NO_DECISION または不合格のため、この読み出しを主枠から撤去せよ。"
            if remove_panel
            else "既存 artifact を変更せず、confirmed の新 version を発行する。"
        ),
    }


def prospective_report(
    session,
    *,
    artifact,
    as_of: datetime.date | None = None,
    bootstrap_seed: int = PROSPECTIVE_BOOTSTRAP_SEED,
    bootstrap_b: int = PROSPECTIVE_BOOTSTRAP_B,
) -> dict[str, Any]:
    """Build the preregistered US5 confirmation report; an empty cohort is valid."""

    report_date = as_of or datetime.date.today()
    if bootstrap_b <= 0:
        raise ValueError("bootstrap_b must be positive")
    valid_from = artifact.valid_from
    if isinstance(valid_from, str):
        valid_from = datetime.date.fromisoformat(valid_from)
    rows = load_prospective_rows(
        session,
        artifact_digest=artifact.artifact_digest,
        through_date=report_date,
    )
    analyzed, exclusions, primary_horizon = _prospective_analysis_rows(
        rows,
        artifact=artifact,
        as_of=report_date,
    )
    overall = _prospective_metric_group(
        analyzed,
        seed=bootstrap_seed,
        b=bootstrap_b,
    )
    field_size_reports = []
    for bucket_name, _lower, _upper in _FIELD_SIZE_BUCKETS:
        bucket_rows = [
            row
            for row in analyzed
            if _field_size_bucket(row.field_size) == bucket_name
        ]
        if bucket_rows:
            field_size_reports.append(
                {
                    "bucket": bucket_name,
                    **_prospective_metric_group(
                        bucket_rows,
                        seed=bootstrap_seed,
                        b=bootstrap_b,
                    ),
                }
            )
    horizon_reports = []
    for horizon_name, _lower, _upper in _CAPTURE_HORIZON_BUCKETS:
        horizon_rows = [
            row for row in analyzed if row.capture_horizon == horizon_name
        ]
        if horizon_rows:
            horizon_reports.append(
                {
                    "horizon": horizon_name,
                    **_prospective_metric_group(
                        horizon_rows,
                        seed=bootstrap_seed,
                        b=bootstrap_b,
                    ),
                }
            )

    coverage = coverage_report(
        session,
        report_from=valid_from,
        report_to=report_date,
    )
    promotion = _promotion_report(
        analyzed,
        overall,
        preregistration=artifact.preregistration,
        as_of=report_date,
    )
    return {
        "schema_version": "chaos-prospective-v1",
        "header": {
            "role": "PRIMARY PREREGISTERED CONFIRMATION",
            "metric_priority": (
                "reliability, Brier, and log score are primary; "
                "AUC is auxiliary only and never decides"
            ),
            "empty_cohort_is_error": False,
        },
        "window": {
            "valid_from": valid_from.isoformat(),
            "through": report_date.isoformat(),
        },
        "artifact": {
            "version": artifact.version,
            "digest": artifact.artifact_digest,
            "fit_through": str(artifact.fit_through),
            "valid_from": valid_from.isoformat(),
        },
        "bootstrap": {
            "method": "race_day_cluster_bootstrap_ci_v1",
            "cluster": "race_day",
            "seed": bootstrap_seed,
            "b": bootstrap_b,
            "ci_note": DIAGNOSTIC_CI_NOTE,
        },
        "analysis_unit": {
            "one_row_per_race": True,
            "primary_horizon": primary_horizon,
            "latest_row_selection_forbidden": True,
            "within_race_multi_capture_forbidden": True,
        },
        "outcome_audit": {
            "rank_basis": "frozen_snapshot_popularity",
            "live_race_horses_popularity_used": False,
            "definition": "top3_popularity_composition_proxy_v1",
        },
        "cohort": {
            "loaded_readouts": len(rows),
            "unique_loaded_races": len({row.race_id for row in rows}),
            "analyzed_races": len(analyzed),
            "race_days": len({row.day for row in analyzed}),
            "capture_strength_required": "confirmatory",
            "exclusions": exclusions,
        },
        "overall": overall,
        "by_field_size": field_size_reports,
        "by_capture_horizon": horizon_reports,
        "promotion": promotion,
        "event_roles": {
            "s_ge_20": "controls_promotion",
            "himo_are": "secondary",
            "total_collapse": (
                "not_eligible; lambda cannot calibrate the first-place marginal"
            ),
            "s_ge_30": "diagnostic_only; never blocks promotion",
        },
        "lambda_limit_note": (
            "total_collapse is not promotion-eligible: lambda1 pins the first-place "
            "marginal to q (measured max |difference| 5.6e-17)."
        ),
        "required_sample_estimates": {
            key: dict(value)
            for key, value in _REQUIRED_SAMPLE_ESTIMATES.items()
        },
        "required_sample_basis": {
            "minimum_positives": 100,
            "reference_year": 2025,
            "race_days": 109,
            "races": 3455,
            "race_day_cluster_design_effect": [1.5, 2.5],
        },
        "capture_coverage": coverage,
        "excluded_race_characteristics": coverage[
            "not_captured_characteristics"
        ],
    }


def diagnose(
    session,
    *,
    diagnose_from: datetime.date,
    diagnose_to: datetime.date,
    artifact,
    bootstrap_seed: int = DIAGNOSTIC_BOOTSTRAP_SEED,
    bootstrap_b: int = DIAGNOSTIC_BOOTSTRAP_B,
    export_fixture: str | Path | None = None,
) -> dict[str, Any]:
    """Build the secondary closing-history chaos report without fitting or recutting edges."""

    fit_to = datetime.date.fromisoformat(str(artifact.fit_to))
    assert diagnose_from > fit_to, (
        f"diagnose_from ({diagnose_from}) must be strictly after artifact.fit_to ({fit_to})"
    )
    if diagnose_to < diagnose_from:
        raise ValueError("diagnose_to must be on or after diagnose_from")
    if bootstrap_b <= 0:
        raise ValueError("bootstrap_b must be positive")

    all_races = load_fit_races(
        session,
        fit_from=diagnose_from,
        fit_to=diagnose_to,
    )
    eligible_races, excluded_counts = partition_eligible_races(all_races)
    stage_discount = StageDiscount(
        lambda2=float(artifact.lambda2),
        lambda3=float(artifact.lambda3),
    )
    rows: list[ChaosDiagnosticRow] = []
    outcome_exclusions: Counter[str] = Counter()
    for race in eligible_races:
        q, ranks = _market_inputs(race)
        outcome_ranks, outcome_reason = _outcome_ranks(race, ranks)
        if outcome_ranks is None:
            outcome_exclusions[outcome_reason or "partial_ingest"] += 1
            continue
        try:
            _raw, adjusted, band = chaos_readout(
                q,
                ranks,
                CHAOS_EVENTS_V1,
                stage_discount=stage_discount,
                edges=artifact.quintile_edges,
            )
        except ChaosInvariantError:
            outcome_exclusions["invariant_violation"] += 1
            continue
        entropy = normalized_entropy(list(q.values()))
        if entropy is None:
            outcome_exclusions["entropy_undefined"] += 1
            continue
        ra, rb, rc = outcome_ranks
        rows.append(
            ChaosDiagnosticRow(
                source=race,
                race_id=race.race_id,
                day=race.race_date.isoformat(),
                field_size=len(race.horses),
                band=band,
                s=ra + rb + rc,
                normalized_entropy=entropy,
                expected_s=adjusted.expected_s,
                probabilities={
                    event.key: adjusted.event_mass[event.key]
                    for event in CHAOS_EVENTS_V1
                },
                outcomes={
                    event.key: int(
                        event.predicate(ra, rb, rc, len(race.horses))
                    )
                    for event in CHAOS_EVENTS_V1
                },
            )
        )
    if not rows:
        raise ArtifactFitError("no outcome-eligible races in the diagnostic window")

    baselines = {
        event.key: _baseline_probabilities(rows, event)
        for event in CHAOS_EVENTS_V1
    }
    band_summary: list[dict[str, Any]] = []
    band_field_size_means: dict[str, float | None] = {}
    for band in _BAND_IDS:
        band_rows = [row for row in rows if row.band == band]
        if band_rows:
            field_size_mean = math.fsum(row.field_size for row in band_rows) / len(
                band_rows
            )
            median_s = float(np.median([row.s for row in band_rows]))
        else:
            field_size_mean = None
            median_s = None
        band_field_size_means[band] = field_size_mean
        band_summary.append(
            {
                "band": band,
                "n": len(band_rows),
                "mean_field_size": field_size_mean,
                "median_s": median_s,
                "events": {
                    event.key: (
                        _score_report(
                            [row.day for row in band_rows],
                            [row.probabilities[event.key] for row in band_rows],
                            [row.outcomes[event.key] for row in band_rows],
                            event,
                            seed=bootstrap_seed,
                            b=bootstrap_b,
                        )
                        if band_rows
                        else _empty_score_report(event)
                    )
                    for event in CHAOS_EVENTS_V1
                },
            }
        )

    overall_events: dict[str, Any] = {}
    baseline_reports: dict[str, Any] = {}
    for event in CHAOS_EVENTS_V1:
        days = [row.day for row in rows]
        outcomes = [row.outcomes[event.key] for row in rows]
        chaos_probabilities = [row.probabilities[event.key] for row in rows]
        overall_events[event.key] = _score_report(
            days,
            chaos_probabilities,
            outcomes,
            event,
            seed=bootstrap_seed,
            b=bootstrap_b,
        )
        baseline_reports[event.key] = {
            "n_only": _score_report(
                days,
                baselines[event.key]["n_only"],
                outcomes,
                event,
                seed=bootstrap_seed,
                b=bootstrap_b,
            ),
            "g_h_n": _score_report(
                days,
                baselines[event.key]["g_h_n"],
                outcomes,
                event,
                seed=bootstrap_seed,
                b=bootstrap_b,
            ),
            "paired_deltas": {
                "chaos_minus_n_only": _paired_loss_deltas(
                    days,
                    chaos_probabilities,
                    baselines[event.key]["n_only"],
                    outcomes,
                    seed=bootstrap_seed,
                    b=bootstrap_b,
                ),
                "chaos_minus_g_h_n": _paired_loss_deltas(
                    days,
                    chaos_probabilities,
                    baselines[event.key]["g_h_n"],
                    outcomes,
                    seed=bootstrap_seed,
                    b=bootstrap_b,
                ),
            },
        }

    fixture_report = (
        export_chaos_diagnostic_fixture(rows, export_fixture)
        if export_fixture is not None
        else None
    )
    return {
        "schema_version": "chaos-diagnose-v1",
        "header": {
            "role": "SECONDARY — not an adoption gate",
            "data_status": "2024+ is discovery data",
            "metric_priority": (
                "reliability, Brier, and log score are primary; AUC is auxiliary only"
            ),
            "can_adopt": False,
            "can_recut_band_edges": False,
        },
        "window": {
            "from": diagnose_from.isoformat(),
            "to": diagnose_to.isoformat(),
            "asserted_after_fit_to": fit_to.isoformat(),
        },
        "artifact": {
            "version": artifact.version,
            "digest": artifact.artifact_digest,
            "fit_to": str(artifact.fit_to),
            "quintile_edges": list(artifact.quintile_edges),
            "edges_read_only": True,
        },
        "bootstrap": {
            "method": "race_day_cluster_bootstrap_ci_v1",
            "cluster": "race_day",
            "seed": bootstrap_seed,
            "b": bootstrap_b,
            "ci_note": DIAGNOSTIC_CI_NOTE,
        },
        "population": {
            "loaded_races": len(all_races),
            "eligible_market_races": len(eligible_races),
            "analyzed_races": len(rows),
            "market_exclusions": excluded_counts,
            "outcome_exclusions": dict(sorted(outcome_exclusions.items())),
        },
        "event_definitions": [
            {
                "key": event.key,
                "label_ja": event.label_ja,
                "promotion_role": event.promotion_role,
                "min_positives_for_decision": event.min_positives_for_decision,
            }
            for event in CHAOS_EVENTS_V1
        ],
        "band_summary": band_summary,
        "band_field_size_means": band_field_size_means,
        "overall": {
            "events": overall_events,
            "metric_priority": (
                "reliability/Brier/log_score PRIMARY; AUC AUXILIARY_ONLY"
            ),
        },
        "baselines": {
            "fit_scope": (
                "descriptive logistic fits on this discovery window; never an adoption gate"
            ),
            "n_only_definition": "logistic(N)",
            "g_h_n_definition": "logistic(normalized_entropy(q), N)",
            "events": baseline_reports,
        },
        "within_field_size_buckets": _within_field_size_report(
            rows,
            baselines,
            seed=bootstrap_seed,
            b=bootstrap_b,
        ),
        "capture_horizon": {
            "status": "not_available",
            "reason": (
                "this secondary diagnostic uses closing-history discovery data, "
                "not prospective snapshots"
            ),
        },
        "fixture_export": fixture_report,
    }


__all__ = [
    "ARTIFACT_VERSION",
    "ArtifactAlreadyExistsError",
    "ArtifactDigestError",
    "ArtifactFitError",
    "BAND_AXIS",
    "ChaosArtifactError",
    "ChaosCoverageRace",
    "ChaosDiagnosticRow",
    "ChaosFitHorse",
    "ChaosFitRace",
    "DIAGNOSTIC_BOOTSTRAP_B",
    "DIAGNOSTIC_BOOTSTRAP_SEED",
    "DIAGNOSTIC_CI_NOTE",
    "ELIGIBILITY_PREDICATE",
    "FrozenChaosHorse",
    "InvalidValidityWindowError",
    "NumericStabilityError",
    "OPERATIONAL_LAMBDA_ENVELOPE",
    "OperationalLambdaError",
    "PROSPECTIVE_BOOTSTRAP_B",
    "PROSPECTIVE_BOOTSTRAP_SEED",
    "ProspectiveAnalysisRow",
    "ProspectiveChaosRace",
    "PublishedChaosArtifact",
    "build_field_size_reference_quantiles",
    "compute_artifact_digest",
    "coverage_report",
    "diagnose",
    "eligibility_exclusion_reason",
    "export_chaos_diagnostic_fixture",
    "fit_artifact",
    "load_coverage_rows",
    "load_fit_races",
    "load_prospective_rows",
    "partition_eligible_races",
    "prospective_report",
    "publish_artifact",
    "run_numeric_stability_gate",
    "within_field_size_percentile",
]
