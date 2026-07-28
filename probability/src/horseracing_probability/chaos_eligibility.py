"""Pure Feature 086 eligibility and capture-horizon derivations.

This module deliberately has no database dependency.  Callers freeze the
current started-entry set and pass it in with the snapshot being evaluated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_EDGES = [1_800, 3_600, 10_800, 21_600, 43_200]


def primary_horizon(artifact: Any) -> dict[str, Any]:
    """Return the required primary horizon in its stable reporting shape.

    The artifact loader owns validation and supplies no fallback.  In
    particular, ``maximum_seconds_to_post`` is always an integer.
    """

    horizon = artifact.preregistration["primary_horizon"]
    return {
        "mode": "artifact_seconds_to_post_window",
        "minimum_seconds_to_post": horizon["minimum_seconds_to_post"],
        "maximum_seconds_to_post": horizon["maximum_seconds_to_post"],
    }


def within_primary_horizon(
    seconds_to_post: int,
    horizon: Mapping[str, Any],
) -> bool:
    """Return whether the observation lies within both inclusive bounds."""

    minimum = int(horizon["minimum_seconds_to_post"])
    maximum = int(horizon["maximum_seconds_to_post"])
    return minimum <= seconds_to_post <= maximum


def display_eligible(snapshot: Any, started_now: Iterable[Any]) -> bool:
    """Return whether an active frozen snapshot still describes the field."""

    if snapshot.status != "active" or snapshot.seconds_to_post is None:
        return False
    return _entry_set(snapshot.field) == _entry_set(started_now)


def confirmation_eligible(snapshot: Any, artifact: Any, started_now: Iterable[Any]) -> bool:
    """Return whether a displayable snapshot belongs to the confirmation cohort."""

    if not display_eligible(snapshot, started_now):
        return False
    if snapshot.capture_strength != "confirmatory":
        return False
    return within_primary_horizon(
        snapshot.seconds_to_post,
        artifact.preregistration["primary_horizon"],
    )


def capture_horizon_buckets(
    minimum: int,
    maximum: int,
) -> list[tuple[str, int, int | None]]:
    """Enumerate labels and bounds derived from both ends of the artifact window.

    The final descriptive bucket stays open.  Confirmation eligibility closes
    the artifact maximum before this descriptive classification is used.
    """

    edges = [edge for edge in _EDGES if minimum < edge < maximum]
    bounds = [minimum, *edges]
    buckets: list[tuple[str, int, int | None]] = []
    for index, lower in enumerate(bounds):
        upper = bounds[index + 1] - 1 if index + 1 < len(bounds) else None
        buckets.append((_capture_horizon_label(lower, upper), lower, upper))
    return buckets


def capture_horizon_bucket(
    seconds_to_post: int,
    *,
    minimum_seconds_to_post: int,
    maximum_seconds_to_post: int,
) -> str:
    """Classify one in-window observation without silently rounding it."""

    if not minimum_seconds_to_post <= seconds_to_post <= maximum_seconds_to_post:
        raise ValueError(
            f"seconds_to_post {seconds_to_post!r} is outside capture horizon buckets "
            f"[{minimum_seconds_to_post}, {maximum_seconds_to_post}]"
        )
    for label, lower, upper in capture_horizon_buckets(
        minimum_seconds_to_post,
        maximum_seconds_to_post,
    ):
        if seconds_to_post >= lower and (upper is None or seconds_to_post <= upper):
            return label
    raise ValueError(f"seconds_to_post {seconds_to_post!r} is outside capture horizon buckets")


def _capture_horizon_label(lower: int, upper: int | None) -> str:
    if upper is None:
        if lower >= 3_600:
            return f"{lower // 3_600}h+"
        return f"{lower // 60}m+"
    if upper + 1 <= 3_600:
        return f"{lower // 60}-{(upper + 1) // 60}m"
    return f"{lower // 3_600}-{(upper + 1) // 3_600}h"


def _entry_set(entries: Iterable[Any]) -> set[tuple[str, int]]:
    return {_entry_identity(entry) for entry in entries}


def _entry_identity(entry: Any) -> tuple[str, int]:
    if isinstance(entry, Mapping):
        horse_id = entry["horse_id"]
        horse_number = entry["horse_number"]
    elif hasattr(entry, "horse_id") and hasattr(entry, "horse_number"):
        horse_id = entry.horse_id
        horse_number = entry.horse_number
    else:
        horse_id, horse_number = entry
    return str(horse_id), int(horse_number)
