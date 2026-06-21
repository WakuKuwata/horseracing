"""CHECK constraint expressions and canonical names.

Single place where SQL CHECK fragments are built, so migrations and any future
validation stay in sync. Names match data-model.md.
"""

from __future__ import annotations

from .enums import (
    AdoptionStatus,
    BetType,
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
