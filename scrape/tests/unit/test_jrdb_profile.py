"""178 T013: the write profiles hold the D7a contract literally (column names spelled out here so
a future addition cannot widen the current DB's allowlist silently)."""

from __future__ import annotations

import pytest

from horseracing_scrape.jrdb import profile
from horseracing_scrape.jrdb.errors import ProfileViolation

#: the four JRDB-defined columns = five physical columns (contracts/files-and-ids.md §4 ✗)
FOUR_COLUMNS = {
    ("race_results", "first_3f"),
    ("race_results", "corner_orders"),
    ("race_horses", "running_style"),
    ("horses", "sire_line"),
    ("horses", "damsire_line"),
}


def test_the_canonical_only_set_is_exactly_the_four_columns():
    assert set(profile.CANONICAL_ONLY_COLUMNS) == FOUR_COLUMNS


@pytest.mark.parametrize(("table", "column"), sorted(FOUR_COLUMNS))
def test_current_profile_never_allows_them(table, column):
    assert not profile.CURRENT.allows(table, column)
    with pytest.raises(ProfileViolation):
        profile.assert_allowed("current", table, column)


@pytest.mark.parametrize(("table", "column"), sorted(FOUR_COLUMNS))
def test_canonical_profile_allows_them(table, column):
    assert profile.JRDB_CANONICAL.allows(table, column)
    profile.assert_allowed(profile.JRDB_CANONICAL, table, column)


def test_current_allowlist_is_spelled_out():
    expected = {
        "races": {
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
        },
        "horses": {
            "horse_name",
            "sex",
            "birth_year",
            "sire_name",
            "dam_name",
            "damsire_name",
            "data_source",
            "owner_name",
            "breeder_name",
        },
        "race_horses": {
            "sex",
            "age",
            "frame",
            "horse_number",
            "jockey_id",
            "trainer_id",
            "weight",
            "weight_diff",
            "odds",
            "popularity",
            "jockey_weight",
            "entry_status",
        },
        "race_results": {
            "finish_order",
            "finish_time",
            "finish_time_diff",
            "last_3f",
            "result_status",
        },
        "race_laps": {"lap_times", "pace_first_3f", "pace_last_3f", "source"},
        "official_win_payouts": {
            "race_id",
            "horse_number",
            "payout_yen",
            "source",
            "observed_at",
            "html_sha256",
        },
        "exotic_odds": {
            "race_id",
            "bet_type",
            "selection",
            "odds",
            "coverage_scope",
            "source",
            "observed_at",
        },
        "id_mappings": {
            "entity_type",
            "source",
            "source_id",
            "canonical_id",
            "mapping_status",
            "conflict_group_id",
            "resolved_at",
            "resolution_note",
        },
        "race_field_provenance": {
            "race_id",
            "field_group",
            "source",
            "official_at",
            "observed_at",
            "ingestion_job_id",
        },
        "race_horse_field_provenance": {
            "race_id",
            "horse_id",
            "field_group",
            "source",
            "observed_at",
            "ingestion_job_id",
        },
        "jrdb_files": {
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
        },
    }
    actual = {t: set(c) for t, c in profile.CURRENT.allowed_columns_by_table.items()}
    assert actual == expected
    # jockeys / trainers are never created by JRDB (ids are mapped, rows are not made)
    assert "jockeys" not in actual and "trainers" not in actual


def test_canonical_is_current_plus_exactly_the_four():
    cur = {(t, c) for t, cols in profile.CURRENT.allowed_columns_by_table.items() for c in cols}
    can = {
        (t, c) for t, cols in profile.JRDB_CANONICAL.allowed_columns_by_table.items() for c in cols
    }
    assert can - cur == FOUR_COLUMNS


def test_unknown_table_or_column_is_a_violation():
    with pytest.raises(ProfileViolation):
        profile.assert_allowed("current", "race_horses", "win_odds_live")
    with pytest.raises(ProfileViolation):
        profile.assert_allowed("current", "jockeys", "jockey_name")


@pytest.mark.parametrize("bad", ["", "production", "CURRENT", "canonical", None])
def test_profile_from_arg_accepts_only_the_two_names(bad):
    with pytest.raises(ValueError):
        profile.profile_from_arg(bad)  # type: ignore[arg-type]
    assert profile.profile_from_arg("current") is profile.CURRENT
    assert profile.profile_from_arg("jrdb_canonical") is profile.JRDB_CANONICAL
    assert profile.includes_canonical("current") is False
    assert profile.includes_canonical("jrdb_canonical") is True


def test_hand_built_profiles_are_refused():
    rogue = profile.WriteProfile("current", {"race_results": frozenset({"first_3f"})})
    with pytest.raises(ValueError):
        profile.profile_from_arg(rogue)
    with pytest.raises(TypeError):
        profile.PROFILES["current"] = rogue  # type: ignore[index]


def test_check_payload_is_the_write_boundary():
    ok = {"finish_order": 1, "finish_time": None}
    assert profile.check_payload("current", "race_results", ok) is ok
    with pytest.raises(ProfileViolation):  # None is still a write (an erase)
        profile.check_payload("current", "race_results", {"finish_order": 1, "first_3f": None})
    profile.check_payload("jrdb_canonical", "race_results", {"first_3f": None})
    with pytest.raises(ProfileViolation):
        profile.check_payload("current", "jockeys", {"jockey_name": "x"})
    assert profile.check_payload("current", "races", {}) == {}
