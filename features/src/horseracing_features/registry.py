"""FeatureRegistry: every feature declares source / availability_timing / missing_policy.

Single source of truth for feature metadata (constitution II). build_feature_matrix
validates produced columns against this registry (fail-fast). Result-time
odds/popularity are deliberately NOT registered as model features (FR-012/FR-013).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AvailabilityTiming(StrEnum):
    PRE_ENTRY = "pre_entry"      # 出馬表前
    POST_FRAME = "post_frame"    # 枠順後
    POST_WEIGHT = "post_weight"  # 馬体重後
    POST_ODDS = "post_odds"      # オッズ後 (MVP 未使用、将来予約)
    PRE_RACE = "pre_race"        # 直前 (MVP 未使用、将来予約)
    POST_RESULT = "post_result"  # 結果後 (モデル入力から機械的に除外)


class MissingPolicy(StrEnum):
    NULL = "null"        # Unknown。0 と区別する
    ZERO_OK = "zero_ok"  # 件数等、0 が意味を持つ


@dataclass(frozen=True)
class FeatureMeta:
    source: str
    timing: AvailabilityTiming
    missing_policy: MissingPolicy


class FeatureSchemaError(ValueError):
    """Raised when a feature column is unregistered or metadata is missing."""


_T = AvailabilityTiming
_M = MissingPolicy

#: name -> FeatureMeta. Ordered; column order of the matrix follows this.
REGISTRY: dict[str, FeatureMeta] = {
    # --- pre-race static ---
    "venue_code": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "distance": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "track_type": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "going": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "weather": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "race_class": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "race_number": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "age": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "sex": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "frame": FeatureMeta("race_horses", _T.POST_FRAME, _M.NULL),
    "horse_number": FeatureMeta("race_horses", _T.POST_FRAME, _M.NULL),
    "jockey_id": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "trainer_id": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "weight": FeatureMeta("race_horses", _T.POST_WEIGHT, _M.NULL),
    "weight_diff": FeatureMeta("race_horses", _T.POST_WEIGHT, _M.NULL),
    # --- past-performance cumulative (as-of race_date < R, finished-only) ---
    "career_starts": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "days_since_last": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "prev_finish": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "prev_last3f": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "avg_finish": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- history counts (non-finishers, separate series, 0 meaningful) ---
    "cancel_count": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "exclude_count": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "stop_count": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "prev_was_cancel": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "prev_was_exclude": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "prev_was_stop": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    # --- flags ---
    "has_past_race": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "is_debut": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "past_race_count": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    "is_low_history": FeatureMeta("history", _T.PRE_ENTRY, _M.ZERO_OK),
    # --- Feature 020: recent form (as-of, rolling last-N finished) ---
    "avg_last3_finish": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "recent_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 020: aptitude (as-of, conditional cumulative before) ---
    "dist_band_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "dist_band_avg_finish": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "surface_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 020: race condition ---
    "class_transition": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "field_size": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.ZERO_OK),
    # --- Feature 020: human form (cross-horse as-of, target-row + same-day excluded) ---
    "jockey_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "trainer_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
}

#: Feature 020: column → group, for ablation (NOT used to select adopted features; the candidate set
#: is fixed a priori). Pre-existing Feature-004 columns are implicitly group "base".
FEATURE_GROUPS: dict[str, str] = {
    "avg_last3_finish": "recent_form",
    "recent_win_rate": "recent_form",
    "dist_band_win_rate": "aptitude",
    "dist_band_avg_finish": "aptitude",
    "surface_win_rate": "aptitude",
    "class_transition": "race_condition",
    "field_size": "race_condition",
    "jockey_win_rate": "human_form",
    "trainer_win_rate": "human_form",
}

#: feature schema version bumped by Feature 020 (passed to model_versions at train time).
FEATURE_VERSION = "features-005"

#: identifier columns present in the matrix but NOT model features.
IDENTIFIER_COLUMNS: tuple[str, ...] = ("race_id", "horse_id")


def model_input_features() -> list[str]:
    """Registered features excluding post_result timing (INV-F5) and identifiers."""
    return [
        name
        for name, meta in REGISTRY.items()
        if meta.timing != AvailabilityTiming.POST_RESULT
    ]


def validate_columns(columns: list[str]) -> None:
    """Fail-fast if any non-identifier column is unregistered (INV-F4)."""
    unknown = [c for c in columns if c not in REGISTRY and c not in IDENTIFIER_COLUMNS]
    if unknown:
        raise FeatureSchemaError(f"unregistered feature columns: {unknown}")
