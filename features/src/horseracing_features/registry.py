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
    # --- Feature 023: pace/time (as-of past races only; in-race relative normalization) ---
    "rel_last3f_avg": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "rel_last3f_best": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "rel_time_avg": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "finish_diff_avg": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "finish_diff_best": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 023: position/style (optional, ablation-gated; past races only) ---
    "rel_corner_pos_avg": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "front_runner_rate": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "closer_rate": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 026: sire aptitude (as-of, OTHER offspring only = sire cumsum − self cumsum) ---
    "sire_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "sire_avg_finish": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "sire_starts": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.ZERO_OK),
    "sire_dist_band_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "sire_surface_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 026: damsire (BMS) aptitude (optional, ablation-gated; overall only) ---
    "damsire_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "damsire_avg_finish": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 030: handicap (斤量, static + 1 as-of change) ---
    "carried_weight": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "carried_weight_ratio": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "carried_weight_rel": FeatureMeta("race_horses", _T.PRE_ENTRY, _M.NULL),
    "carried_weight_change": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 030: season (static, from race_date) ---
    "race_month": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    "race_season": FeatureMeta("races", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 030: place_rate (as-of self, strictly-before) ---
    "place_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "show_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "dist_band_place_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 030: human_form_plus (as-of cross, target-row + same-day excluded) ---
    "jockey_place_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "trainer_place_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "jockey_recent_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "jockey_surface_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "jt_combo_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "jockey_change": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 030: course_aptitude (as-of self venue) ---
    "venue_win_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    "venue_place_rate": FeatureMeta("history", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 031: pace_scenario (field-composition, leave-one-out over started field) ---
    "field_front_rate_ex_self": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "field_closer_rate_ex_self": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "pace_imbalance_ex_self": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "front_pressure": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "closer_setup": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "style_mismatch": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    "field_style_coverage": FeatureMeta("pace", _T.PRE_ENTRY, _M.NULL),
    # --- Feature 032: debut/low-history × pedigree ---
    "sire_debut_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "debut_x_sire_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "debut_x_sire_dist_band_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "lowhist_x_sire_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
    "lowhist_x_sire_dist_band_win_rate": FeatureMeta("pedigree", _T.PRE_ENTRY, _M.NULL),
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
    # Feature 023: pace/time (MVP main group)
    "rel_last3f_avg": "pace_time",
    "rel_last3f_best": "pace_time",
    "rel_time_avg": "pace_time",
    "finish_diff_avg": "pace_time",
    "finish_diff_best": "pace_time",
    # Feature 023: position/style (optional, ablation-gated)
    "rel_corner_pos_avg": "position_style",
    "front_runner_rate": "position_style",
    "closer_rate": "position_style",
    # Feature 026: sire aptitude (MVP main group)
    "sire_win_rate": "sire_aptitude",
    "sire_avg_finish": "sire_aptitude",
    "sire_starts": "sire_aptitude",
    "sire_dist_band_win_rate": "sire_aptitude",
    "sire_surface_win_rate": "sire_aptitude",
    # Feature 026: damsire/BMS aptitude (optional, ablation-gated)
    "damsire_win_rate": "damsire_aptitude",
    "damsire_avg_finish": "damsire_aptitude",
    # Feature 030: handicap (斤量)
    "carried_weight": "handicap",
    "carried_weight_ratio": "handicap",
    "carried_weight_rel": "handicap",
    "carried_weight_change": "handicap",
    # Feature 030: season
    "race_month": "season",
    "race_season": "season",
    # Feature 030: place_rate (複勝率)
    "place_rate": "place_rate",
    "show_rate": "place_rate",
    "dist_band_place_rate": "place_rate",
    # Feature 030: human_form_plus
    "jockey_place_rate": "human_form_plus",
    "trainer_place_rate": "human_form_plus",
    "jockey_recent_win_rate": "human_form_plus",
    "jockey_surface_win_rate": "human_form_plus",
    "jt_combo_win_rate": "human_form_plus",
    "jockey_change": "human_form_plus",
    # Feature 030: course_aptitude
    "venue_win_rate": "course_aptitude",
    "venue_place_rate": "course_aptitude",
    # Feature 031: pace_scenario (field-composition + own-style interaction)
    "field_front_rate_ex_self": "pace_scenario",
    "field_closer_rate_ex_self": "pace_scenario",
    "pace_imbalance_ex_self": "pace_scenario",
    "front_pressure": "pace_scenario",
    "closer_setup": "pace_scenario",
    "style_mismatch": "pace_scenario",
    "field_style_coverage": "pace_scenario",
    # Feature 032: debut/low-history × pedigree
    "sire_debut_win_rate": "debut_pedigree",
    "debut_x_sire_win_rate": "debut_pedigree",
    "debut_x_sire_dist_band_win_rate": "debut_pedigree",
    "lowhist_x_sire_win_rate": "debut_pedigree",
    "lowhist_x_sire_dist_band_win_rate": "debut_pedigree",
}

#: feature schema version. 023/026/030/031; bumped by 032 (debut/low-history × pedigree).
FEATURE_VERSION = "features-010"

#: identifier columns present in the matrix but NOT model features.
IDENTIFIER_COLUMNS: tuple[str, ...] = ("race_id", "horse_id")


#: Feature 025: current-race/static features computed by build_static_features (NOT materialized —
#: they come from the target race row only and are cheap). Everything else in REGISTRY is an as-of /
#: past-derived feature that the materialization phase precomputes.
STATIC_COLUMNS: tuple[str, ...] = (
    "venue_code", "distance", "track_type", "going", "weather", "race_class", "race_number",
    "age", "sex", "frame", "horse_number", "jockey_id", "trainer_id", "weight", "weight_diff",
    "field_size",
    # Feature 030: current-race static (斤量・季節) — build_static_features, not materialized.
    "carried_weight", "carried_weight_ratio", "carried_weight_rel", "race_month", "race_season",
)


def materialized_columns() -> list[str]:
    """Feature 025: as-of/past-derived feature columns to materialize (registry order, static
    excluded). Mechanically derived so a new as-of feature is materialized by default and a static
    one never is."""
    return [name for name in REGISTRY if name not in STATIC_COLUMNS]


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
