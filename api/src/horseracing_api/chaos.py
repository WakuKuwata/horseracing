"""Read-only assembly of the Feature 084 top-3 chaos response.

The API reads the newest active frozen snapshot directly from the DB; it never imports the
``live`` writer. A configured, approved artifact is mandatory. Persisted values are authoritative
when their digest matches that artifact; otherwise values are recomputed from the frozen field and
the digest divergence remains visible in the response.
"""

from __future__ import annotations

import bisect
import datetime
import json
import math
import os
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from horseracing_db.enums import EntryStatus
from horseracing_db.models import ChaosReadout, ChaosSnapshot, RaceHorse
from horseracing_eval.stage_discount import StageDiscount
from horseracing_probability.chaos_artifact import (
    ChaosArtifactApprovalError,
    ChaosArtifactError,
    ChaosArtifactOutOfValidityWindowError,
    ChaosArtifactUnavailableError,
    ChaosBandsArtifact,
    load_chaos_artifact,
    resolve_current_digest,
)
from horseracing_probability.chaos_distribution import (
    ChaosDistribution,
    ChaosInvariantError,
    chaos_readout,
)
from horseracing_probability.chaos_eligibility import display_eligible
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1
from horseracing_probability.market_odds import MarketOddsError, market_implied_win_probs
from sqlalchemy import select
from sqlalchemy.orm import Session

from .schemas import (
    ChaosEvent,
    ChaosSnapshotProvenance,
    RaceChaosAvailable,
    RaceChaosUnavailable,
)

CHAOS_ARTIFACT_PATH_ENV = "CHAOS_BANDS_ARTIFACT_PATH"
CHAOS_APPROVED_MANIFEST_ENV = "CHAOS_BANDS_APPROVED_MANIFEST"

UnavailableReason = Literal[
    "no_snapshot",
    "partial_market_odds",
    "invalid_popularity_ranks",
    "field_too_small",
    "field_changed_after_capture",
    "artifact_unavailable",
    "out_of_validity_window",
    "invariant_violation",
]

_EVENT_DEFINITIONS = {event.key: event for event in CHAOS_EVENTS_V1}
_CACHE_MAX_SIZE = 1024
_DERIVED_CACHE: OrderedDict[
    tuple[str, str],
    tuple[ChaosDistribution, ChaosDistribution, str],
] = OrderedDict()
_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class _FrozenField:
    odds: dict[str, float]
    ranks: dict[str, int]
    n: int


@dataclass(frozen=True)
class _ReadoutValues:
    band: str
    p_s_ge_20: float
    p_himo_are: float
    p_total_collapse: float
    raw_p_s_ge_20: float
    raw_p_himo_are: float
    raw_p_total_collapse: float
    expected_s: float
    structural_zeros: Mapping[str, Any]


def clear_chaos_cache() -> None:
    """Clear the process-local derivation cache (primarily for deterministic tests)."""

    with _CACHE_LOCK:
        _DERIVED_CACHE.clear()


def _unavailable(reason: UnavailableReason) -> RaceChaosUnavailable:
    return RaceChaosUnavailable(
        status="unavailable",
        unavailable_reason=reason,
        band_axis="p_s_ge_20",
    )


def _latest_active_snapshot(session: Session, race_id: str) -> ChaosSnapshot | None:
    """SNAP-6: newest captured active row; void rows are excluded at the query boundary."""

    return session.scalars(
        select(ChaosSnapshot)
        .where(ChaosSnapshot.race_id == race_id)
        .where(ChaosSnapshot.status == "active")
        .order_by(
            ChaosSnapshot.captured_at.desc(),
            ChaosSnapshot.chaos_snapshot_id.desc(),
        )
        .limit(1)
    ).first()


def _snapshot_for_race(session: Session, race_id: str) -> ChaosSnapshot | None:
    """Return the one frozen observation regardless of its active/void display status."""

    return session.scalars(
        select(ChaosSnapshot)
        .where(ChaosSnapshot.race_id == race_id)
        .order_by(
            ChaosSnapshot.captured_at.desc(),
            ChaosSnapshot.chaos_snapshot_id.desc(),
        )
        .limit(1)
    ).first()


def _started_field_for_race(session: Session, race_id: str) -> Sequence[Any]:
    """Read the current started-entry identities without consulting mutable odds."""

    return session.execute(
        select(RaceHorse.horse_id, RaceHorse.horse_number)
        .where(RaceHorse.race_id == race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()


def _matching_readout(
    session: Session,
    snapshot_id: Any,
    artifact_digest: str,
) -> ChaosReadout | None:
    return session.scalars(
        select(ChaosReadout)
        .where(ChaosReadout.chaos_snapshot_id == snapshot_id)
        .where(ChaosReadout.artifact_digest == artifact_digest)
        .order_by(
            ChaosReadout.computed_at.desc(),
            ChaosReadout.chaos_readout_id.desc(),
        )
        .limit(1)
    ).first()


def _latest_readout(session: Session, snapshot_id: Any) -> ChaosReadout | None:
    return session.scalars(
        select(ChaosReadout)
        .where(ChaosReadout.chaos_snapshot_id == snapshot_id)
        .order_by(
            ChaosReadout.computed_at.desc(),
            ChaosReadout.chaos_readout_id.desc(),
        )
        .limit(1)
    ).first()


def _approved_digests(manifest_path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Read only the committed approval list; payload interpretation stays in probability."""

    path = Path(manifest_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["approved"]
        if not isinstance(rows, list):
            raise TypeError("approved must be a list")
        digests = tuple(str(row["digest"]) for row in rows)
    except (OSError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise ChaosArtifactUnavailableError(
            f"invalid chaos approval manifest {path}: {exc}"
        ) from exc
    if not digests or any(
        len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)
        for digest in digests
    ):
        raise ChaosArtifactUnavailableError(
            f"chaos approval manifest has no valid digest: {path}"
        )
    return digests


def _load_configured_artifact(
    target_date: datetime.date | None,
    *,
    artifact_path: str | os.PathLike[str] | None = None,
    manifest_path: str | os.PathLike[str] | None = None,
) -> ChaosBandsArtifact:
    """Load the explicitly configured current artifact; no repository default is allowed."""

    configured_artifact = artifact_path or os.environ.get(CHAOS_ARTIFACT_PATH_ENV)
    configured_manifest = manifest_path or os.environ.get(CHAOS_APPROVED_MANIFEST_ENV)
    if not configured_artifact or not configured_manifest:
        raise ChaosArtifactUnavailableError(
            f"{CHAOS_ARTIFACT_PATH_ENV} and {CHAOS_APPROVED_MANIFEST_ENV} are required"
        )
    if target_date is None:
        raise ChaosArtifactOutOfValidityWindowError("race target_date is unavailable")
    approved = _approved_digests(configured_manifest)
    artifact = load_chaos_artifact(
        configured_artifact,
        approved_digests=approved,
        target_date=target_date,
    )
    # "approved" (listed in the manifest) and "current" (status=active) are DIFFERENT concepts.
    # 084 compared against approved[-1], and since the manifest lists active first and
    # superseded last, that read the SUPERSEDED artifact. Resolve on status instead.
    if artifact.artifact_digest != resolve_current_digest(configured_manifest):
        raise ChaosArtifactApprovalError(
            "configured chaos artifact is approved but is not the manifest's current digest"
        )
    return artifact


def _validate_snapshot_field(
    snapshot: ChaosSnapshot,
) -> tuple[_FrozenField | None, UnavailableReason | None]:
    field = snapshot.field
    if not isinstance(field, Sequence) or isinstance(field, (str, bytes)):
        return None, "invalid_popularity_ranks"
    if len(field) < 4:
        return None, "field_too_small"
    if snapshot.n != len(field):
        return None, "invalid_popularity_ranks"

    odds: dict[str, float] = {}
    ranks: dict[str, int] = {}
    for row in field:
        if not isinstance(row, Mapping):
            return None, "invalid_popularity_ranks"
        horse_id = row.get("horse_id")
        if not isinstance(horse_id, str) or not horse_id or horse_id in ranks:
            return None, "invalid_popularity_ranks"

        odds_value = row.get("odds")
        if isinstance(odds_value, bool):
            return None, "partial_market_odds"
        try:
            numeric_odds = float(odds_value)
        except (TypeError, ValueError):
            return None, "partial_market_odds"
        if not math.isfinite(numeric_odds) or numeric_odds <= 0.0:
            return None, "partial_market_odds"

        popularity = row.get("popularity")
        if (
            isinstance(popularity, bool)
            or not isinstance(popularity, int)
            or popularity <= 0
        ):
            return None, "invalid_popularity_ranks"
        odds[horse_id] = numeric_odds
        ranks[horse_id] = popularity

    if len(set(ranks.values())) != len(ranks):
        return None, "invalid_popularity_ranks"
    return _FrozenField(odds=odds, ranks=ranks, n=len(field)), None


def _derive_cached(
    field: _FrozenField,
    *,
    content_digest: str,
    artifact: ChaosBandsArtifact,
) -> tuple[ChaosDistribution, ChaosDistribution, str]:
    """Run the raw and adjusted engines once per content/artifact pair."""

    key = (content_digest, artifact.artifact_digest)
    with _CACHE_LOCK:
        cached = _DERIVED_CACHE.get(key)
        if cached is not None:
            _DERIVED_CACHE.move_to_end(key)
            return cached

        q = market_implied_win_probs(field.odds)
        derived = chaos_readout(
            q,
            field.ranks,
            CHAOS_EVENTS_V1,
            stage_discount=StageDiscount(
                lambda2=artifact.lambda2,
                lambda3=artifact.lambda3,
            ),
            edges=artifact.quintile_edges,
        )
        _DERIVED_CACHE[key] = derived
        if len(_DERIVED_CACHE) > _CACHE_MAX_SIZE:
            _DERIVED_CACHE.popitem(last=False)
        return derived


def _within_field_size_percentile(
    p_s_ge_20: float,
    field_size: int,
    references: Mapping[str, Mapping[str, Any]],
) -> float | None:
    """Interpolate the frozen within-field-size quantiles without importing training."""

    reference = references.get(str(field_size))
    if reference is None:
        return None
    try:
        levels = [float(value) for value in reference["quantile_levels"]]
        values = [float(value) for value in reference["p_s_ge_20"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("field-size reference quantiles are malformed") from exc
    if (
        len(levels) != len(values)
        or not levels
        or any(not math.isfinite(value) for value in (*levels, *values))
        or any(right < left for left, right in zip(levels, levels[1:], strict=False))
        or any(right < left for left, right in zip(values, values[1:], strict=False))
        or not math.isfinite(p_s_ge_20)
    ):
        raise ValueError("field-size reference quantiles are malformed")

    equal = [index for index, value in enumerate(values) if value == p_s_ge_20]
    if equal:
        return 100.0 * (levels[equal[0]] + levels[equal[-1]]) / 2.0
    if p_s_ge_20 < values[0]:
        return 0.0
    if p_s_ge_20 > values[-1]:
        return 100.0
    right = bisect.bisect_right(values, p_s_ge_20)
    left = right - 1
    fraction = (p_s_ge_20 - values[left]) / (values[right] - values[left])
    return 100.0 * (levels[left] + fraction * (levels[right] - levels[left]))


def _persisted_values(readout: ChaosReadout) -> _ReadoutValues:
    return _ReadoutValues(
        band=str(readout.band),
        p_s_ge_20=float(readout.p_s_ge_20),
        p_himo_are=float(readout.p_himo_are),
        p_total_collapse=float(readout.p_total_collapse),
        raw_p_s_ge_20=float(readout.raw_p_s_ge_20),
        raw_p_himo_are=float(readout.raw_p_himo_are),
        raw_p_total_collapse=float(readout.raw_p_total_collapse),
        expected_s=float(readout.expected_s),
        structural_zeros=readout.structural_zeros,
    )


def _derived_values(
    raw: ChaosDistribution,
    adjusted: ChaosDistribution,
    band: str,
) -> _ReadoutValues:
    return _ReadoutValues(
        band=band,
        p_s_ge_20=adjusted.event_mass["s_ge_20"],
        p_himo_are=adjusted.event_mass["himo_are"],
        p_total_collapse=adjusted.event_mass["total_collapse"],
        raw_p_s_ge_20=raw.event_mass["s_ge_20"],
        raw_p_himo_are=raw.event_mass["himo_are"],
        raw_p_total_collapse=raw.event_mass["total_collapse"],
        expected_s=adjusted.expected_s,
        structural_zeros=adjusted.structural_zero,
    )


def _event(
    key: Literal["s_ge_20", "himo_are", "total_collapse"],
    *,
    adjusted_mass: float,
    raw_mass: float,
    structural_zeros: Mapping[str, Any],
) -> ChaosEvent:
    definition = _EVENT_DEFINITIONS[key]
    reason = structural_zeros.get(key)
    return ChaosEvent(
        key=key,
        label_ja=definition.label_ja,
        adjusted_mass=adjusted_mass,
        raw_mass=raw_mass,
        is_structural_zero=key in structural_zeros,
        structural_zero_reason=(str(reason) if reason is not None else None),
        lambda_sensitive=definition.lambda_sensitive,
    )


def _available(
    *,
    snapshot: ChaosSnapshot,
    artifact: ChaosBandsArtifact,
    field_size: int,
    values: _ReadoutValues,
    readout_source: Literal["persisted", "recomputed"],
    persisted_artifact_digest: str | None,
) -> RaceChaosAvailable:
    support = (6, 3 * field_size - 3)
    percentile = _within_field_size_percentile(
        values.p_s_ge_20,
        field_size,
        artifact.field_size_reference_quantiles,
    )
    return RaceChaosAvailable(
        status="available",
        unavailable_reason=None,
        band=values.band,
        band_axis="p_s_ge_20",
        field_size=field_size,
        feasible_support=support,
        feasible_support_ja=f"人気合計は {support[0]}〜{support[1]} の範囲",
        events=[
            _event(
                "s_ge_20",
                adjusted_mass=values.p_s_ge_20,
                raw_mass=values.raw_p_s_ge_20,
                structural_zeros=values.structural_zeros,
            ),
            _event(
                "himo_are",
                adjusted_mass=values.p_himo_are,
                raw_mass=values.raw_p_himo_are,
                structural_zeros=values.structural_zeros,
            ),
            _event(
                "total_collapse",
                adjusted_mass=values.p_total_collapse,
                raw_mass=values.raw_p_total_collapse,
                structural_zeros=values.structural_zeros,
            ),
        ],
        expected_top3_popularity_sum=values.expected_s,
        within_field_size_percentile=percentile,
        calibration_status=artifact.calibration_status,
        calibration_basis=(
            f"closing_history_{artifact.fit_from[:4]}_{artifact.fit_to[:4]}"
        ),
        is_market_derived=True,
        is_pseudo=True,
        snapshot=ChaosSnapshotProvenance(
            captured_at=snapshot.captured_at,
            source=snapshot.source,
            seconds_to_post=snapshot.seconds_to_post,
            capture_strength=snapshot.capture_strength,
            content_digest=snapshot.content_digest,
            snapshot_id=str(snapshot.chaos_snapshot_id),
        ),
        artifact_version=artifact.version,
        artifact_digest=artifact.artifact_digest,
        readout_source=readout_source,
        persisted_artifact_digest=persisted_artifact_digest,
    )


def build_race_chaos(
    session: Session,
    *,
    race_id: str,
    target_date: datetime.date | None,
    artifact_path: str | os.PathLike[str] | None = None,
    manifest_path: str | os.PathLike[str] | None = None,
) -> RaceChaosAvailable | RaceChaosUnavailable:
    """Build one tagged response without writing or consulting current race-horse odds."""

    snapshot = _snapshot_for_race(session, race_id)
    if snapshot is None:
        return _unavailable("no_snapshot")
    if snapshot.status == "void":
        if snapshot.void_reason == "field_changed":
            return _unavailable("field_changed_after_capture")
        return _unavailable("no_snapshot")

    started_now = _started_field_for_race(session, race_id)
    try:
        if not display_eligible(snapshot, started_now):
            return _unavailable("field_changed_after_capture")
    except (KeyError, TypeError, ValueError):
        return _unavailable("field_changed_after_capture")

    frozen, invalid_reason = _validate_snapshot_field(snapshot)
    if frozen is None:
        assert invalid_reason is not None
        return _unavailable(invalid_reason)

    try:
        artifact = _load_configured_artifact(
            target_date,
            artifact_path=artifact_path,
            manifest_path=manifest_path,
        )
    except ChaosArtifactError as exc:
        return _unavailable(exc.unavailable_reason)

    if (
        artifact.band_axis != "p_s_ge_20"
        or artifact.calibration_status not in {"provisional", "confirmed"}
    ):
        return _unavailable("artifact_unavailable")

    persisted = _matching_readout(
        session,
        snapshot.chaos_snapshot_id,
        artifact.artifact_digest,
    )
    if persisted is not None:
        values = _persisted_values(persisted)
        source: Literal["persisted", "recomputed"] = "persisted"
        persisted_digest = str(persisted.artifact_digest)
    else:
        previous = _latest_readout(session, snapshot.chaos_snapshot_id)
        try:
            raw, adjusted, band = _derive_cached(
                frozen,
                content_digest=snapshot.content_digest,
                artifact=artifact,
            )
        except (ChaosInvariantError, MarketOddsError, OverflowError, ZeroDivisionError, ValueError):
            return _unavailable("invariant_violation")
        values = _derived_values(raw, adjusted, band)
        source = "recomputed"
        persisted_digest = (
            str(previous.artifact_digest) if previous is not None else None
        )

    try:
        return _available(
            snapshot=snapshot,
            artifact=artifact,
            field_size=frozen.n,
            values=values,
            readout_source=source,
            persisted_artifact_digest=persisted_digest,
        )
    except (KeyError, TypeError, ValueError):
        # Nested reference-quantile corruption is an artifact failure, not an API 500.
        return _unavailable("artifact_unavailable")


__all__ = [
    "CHAOS_APPROVED_MANIFEST_ENV",
    "CHAOS_ARTIFACT_PATH_ENV",
    "build_race_chaos",
    "clear_chaos_cache",
]
