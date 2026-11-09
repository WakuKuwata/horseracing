"""Write-target profiles (Feature 178, T010; research D7a; precedence-jrdb §3a).

``CURRENT`` is the production DB: the four JRDB-defined columns (five physical columns) are
*absent* from its allowlist, so a write there fails at the write boundary even if a decoder bug
let a value through. ``JRDB_CANONICAL`` is the rebuilt JRDB-only DB (段 3b) and allows them.

Only the two registered constants exist (codex D20 #10): callers pass a NAME (or one of the two
constants themselves); a hand-built ``WriteProfile`` is refused, and the registry is immutable.

The write boundary is :func:`check_payload` (codex D20 #9): every JRDB write path (daily ingest,
corrected versions, repair, rebuild) hands its ``{column: value}`` payload to it before any SQL,
so a column the profile does not allow — including ``None`` meant to erase a value — cannot
reach the database by forgetting a per-column call. ``assert_allowed`` remains for single
columns. Binding the *connection* to the profile (``jrdb_canonical`` must not point at the
production ``DATABASE_URL``) is the CLI's startup check (tasks T051), not this module's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .errors import ProfileViolation

ProfileName = Literal["current", "jrdb_canonical"]

#: The columns whose JRDB definition differs from the JRA-VAN column of the same name.
CANONICAL_ONLY_COLUMNS: frozenset[tuple[str, str]] = frozenset(
    {
        ("race_results", "first_3f"),
        ("race_results", "corner_orders"),
        ("race_horses", "running_style"),
        ("horses", "sire_line"),
        ("horses", "damsire_line"),
    }
)

#: the same four columns as value-object attributes (decode.py's final guard under ``current``)
CANONICAL_ONLY_ATTRIBUTES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "JrdbResult": ("first_3f", "corner_orders", "running_style_code"),
        "JrdbHorse": ("sire_line_code", "damsire_line_code"),
    }
)

_COMMON: dict[str, frozenset[str]] = {
    "races": frozenset(
        {
            "race_name",
            "race_name_short",
            "venue_code",
            "distance",
            "track_type",
            "race_date",
            "race_number",
            "grade",
            "race_class",
            "weather",
            "going",
            "post_time",
            "prize_money",
        }
    ),
    "horses": frozenset(
        {
            "horse_name",
            "sex",
            "birth_year",
            "sire_name",
            "dam_name",
            "damsire_name",
            "data_source",
            "owner_name",
            "breeder_name",
        }
    ),
    "race_horses": frozenset(
        {
            "sex",
            "age",
            "frame",
            "horse_number",
            "jockey_id",
            "trainer_id",
            "weight",
            "weight_diff",
            "odds",  # 確定単勝オッズ via apply_final_odds only (never live win_odds)
            "popularity",
            "jockey_weight",
            "entry_status",
        }
    ),
    "race_results": frozenset(
        {"finish_order", "finish_time", "finish_time_diff", "last_3f", "result_status"}
    ),
    "race_laps": frozenset({"lap_times", "pace_first_3f", "pace_last_3f", "source"}),
    "official_win_payouts": frozenset(
        {"race_id", "horse_number", "payout_yen", "source", "observed_at", "html_sha256"}
    ),
    "exotic_odds": frozenset(
        {"race_id", "bet_type", "selection", "odds", "coverage_scope", "source", "observed_at"}
    ),
    "id_mappings": frozenset(
        {
            "entity_type",
            "source",
            "source_id",
            "canonical_id",
            "mapping_status",
            "conflict_group_id",
            "resolved_at",
            "resolution_note",
        }
    ),
    "race_field_provenance": frozenset(
        {"race_id", "field_group", "source", "official_at", "observed_at", "ingestion_job_id"}
    ),
    "race_horse_field_provenance": frozenset(
        {"race_id", "horse_id", "field_group", "source", "observed_at", "ingestion_job_id"}
    ),
    "jrdb_files": frozenset(
        {
            "kind",
            "data_date",
            "variant",
            "sha256",
            "size_bytes",
            "record_length",
            "source_url_sha256",
            "published_at",
            "fetched_at",
            "applied_at",
            "status",
            "race_status",
            "ingestion_job_id",
        }
    ),
}


@dataclass(frozen=True)
class WriteProfile:
    name: ProfileName
    allowed_columns_by_table: Mapping[str, frozenset[str]]

    def allows(self, table: str, column: str) -> bool:
        cols = self.allowed_columns_by_table.get(table)
        return cols is not None and column in cols

    @property
    def includes_canonical(self) -> bool:
        return self.name == "jrdb_canonical"


def _with_canonical(base: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    out = {k: set(v) for k, v in base.items()}
    for table, col in CANONICAL_ONLY_COLUMNS:
        out.setdefault(table, set()).add(col)
    return {k: frozenset(v) for k, v in out.items()}


CURRENT = WriteProfile("current", MappingProxyType(dict(_COMMON)))
JRDB_CANONICAL = WriteProfile("jrdb_canonical", MappingProxyType(_with_canonical(_COMMON)))

PROFILES: Mapping[str, WriteProfile] = MappingProxyType(
    {"current": CURRENT, "jrdb_canonical": JRDB_CANONICAL}
)


def profile_from_arg(value: str | WriteProfile) -> WriteProfile:
    """Resolve ``"current"`` / ``"jrdb_canonical"`` (or one of the two constants) to the
    registered constant; anything else — including a hand-built ``WriteProfile`` — is refused."""

    if isinstance(value, WriteProfile):
        if value is CURRENT or value is JRDB_CANONICAL:
            return value
        raise ValueError("write profiles must be one of the registered constants")
    if not isinstance(value, str):
        raise ValueError("write profile must be a name")
    key = value.strip()
    if key not in PROFILES:
        raise ValueError(f"unknown write profile {value!r}; expected one of {sorted(PROFILES)}")
    return PROFILES[key]


def includes_canonical(profile: str | WriteProfile) -> bool:
    return profile_from_arg(profile).includes_canonical


def assert_allowed(profile: str | WriteProfile, table: str, column: str) -> None:
    p = profile_from_arg(profile)
    if not p.allows(table, column):
        raise ProfileViolation(p.name, table, column)


def check_payload(
    profile: str | WriteProfile, table: str, payload: Mapping[str, object]
) -> Mapping[str, object]:
    """The write boundary: validate every column of ``payload`` (``None`` values included — an
    erase is a write) and return the payload unchanged. Raises :class:`ProfileViolation` on the
    first column the profile does not allow; an empty payload is allowed."""

    p = profile_from_arg(profile)
    for column in payload:
        if not p.allows(table, column):
            raise ProfileViolation(p.name, table, column)
    return payload


__all__ = [
    "CANONICAL_ONLY_ATTRIBUTES",
    "CANONICAL_ONLY_COLUMNS",
    "CURRENT",
    "JRDB_CANONICAL",
    "PROFILES",
    "WriteProfile",
    "assert_allowed",
    "check_payload",
    "includes_canonical",
    "profile_from_arg",
]
