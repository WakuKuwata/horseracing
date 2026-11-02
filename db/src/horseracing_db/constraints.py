"""CHECK constraint expressions and canonical names.

Single place where SQL CHECK fragments are built, so migrations and any future
validation stay in sync. Names match data-model.md.
"""

from __future__ import annotations

from .enums import (
    AdoptionStatus,
    BetType,
    CoverageScope,
    EntityType,
    EntryStatus,
    JobStatus,
    MappingStatus,
    ResultStatus,
    Source,
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


# races
RACE_ID_FORMAT = r"race_id ~ '^[0-9]{12}$'"
RACE_NUMBER_RANGE = "race_number >= 1 AND race_number <= 12"

# race_horses / race_results
ENTRY_STATUS = _in_list("entry_status", EntryStatus.ALL)
RESULT_STATUS = _in_list("result_status", ResultStatus.ALL)
# INV-2: finished rows must carry a finish_order.
FINISH_ORDER_WHEN_FINISHED = "result_status <> 'finished' OR finish_order IS NOT NULL"

# race_horses place quote (Phase 0-2): netkeiba publishes 複勝 as a RANGE [low, high]. Either both
# ends are present and ordered, or neither is — a half-stored range is never a valid market quote.
PLACE_ODDS_RANGE = (
    "(place_odds_low IS NULL AND place_odds_high IS NULL) OR "
    "(place_odds_low > 0 AND place_odds_high > 0 AND place_odds_low <= place_odds_high)"
)

# id_mappings
MAPPING_STATUS = _in_list("mapping_status", MappingStatus.ALL)
ID_ENTITY_TYPE = _in_list("entity_type", EntityType.ALL)
ID_SOURCE = _in_list("source", Source.ALL)

# ingestion_jobs
JOB_STATUS = _in_list("status", JobStatus.ALL)
JOB_SOURCE = _in_list("source", Source.ALL)

# model_versions
ADOPTION_STATUS = _in_list("adoption_status", AdoptionStatus.ALL)

# race_predictions (single row check: range + monotonicity, 憲法 IV)
PROB_MONOTONIC = (
    "0 <= win_prob AND win_prob <= top2_prob "
    "AND top2_prob <= top3_prob AND top3_prob <= 1"
)

# recommendations
BET_TYPE = _in_list("bet_type", BetType.ALL)

# exotic_odds (012): win excluded (no exotic pool); coverage full/partial
EXOTIC_BET_TYPE = _in_list("bet_type", BetType.EXOTIC)
COVERAGE_SCOPE = _in_list("coverage_scope", CoverageScope.ALL)

# official_win_payouts (139 → 176): the result page a 単勝 payout was read from. Kept separate from
# Source: these name the PAGE KIND, not the feed, and the default stays netkeiba_result.
OFFICIAL_WIN_PAYOUT_SOURCES: tuple[str, ...] = ("netkeiba_result", "jra_official_result")
OFFICIAL_WIN_PAYOUT_SOURCE_CHECK = _in_list("source", OFFICIAL_WIN_PAYOUT_SOURCES)

# provenance tables (176, migration 0023): which field group a row describes
RACE_HORSE_FIELD_GROUPS: tuple[str, ...] = (
    "entry_static", "jockey", "trainer", "body_weight", "entry_status",
)
RACE_FIELD_GROUPS: tuple[str, ...] = (
    "race_static", "post_time", "win_odds", "place_odds", "results", "laps",
    "win_payouts", "exotic_payouts",
)
RACE_HORSE_FIELD_GROUP = _in_list("field_group", RACE_HORSE_FIELD_GROUPS)
RACE_FIELD_GROUP = _in_list("field_group", RACE_FIELD_GROUPS)

# jra_track_conditions (176, migration 0024)
TRACK_CONDITION_ITEMS: tuple[str, ...] = (
    "moisture_turf_goal", "moisture_turf_corner4", "moisture_dirt_goal", "moisture_dirt_corner4",
    "cushion_turf", "going_turf", "going_dirt", "weather",
)
TRACK_CONDITION_TIME_BASES: tuple[str, ...] = ("measured", "fetched")
TRACK_CONDITION_ITEM = _in_list("item", TRACK_CONDITION_ITEMS)
TRACK_CONDITION_TIME_BASIS = _in_list("time_basis", TRACK_CONDITION_TIME_BASES)
TRACK_CONDITION_VENUE = r"venue_code ~ '^[0-9]{2}$'"
