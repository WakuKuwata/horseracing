"""176 T019/T090: migrations 0023 (jra_official + provenance) and 0024 (track conditions).

SC-009: existing tables keep every column definition; the only schema differences are the five
widened source CHECKs and the three new tables. Downgrade refuses once JRA rows exist.
"""

from __future__ import annotations

import datetime

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

_EXISTING_TABLES_SQL = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name NOT IN
  ('alembic_version', 'race_horse_field_provenance', 'race_field_provenance',
   'jra_track_conditions')
"""
_COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = ANY(:tables)
ORDER BY table_name, ordinal_position
"""
_CHECKS_SQL = """
SELECT c.conrelid::regclass::text AS tbl, c.conname, pg_get_constraintdef(c.oid)
FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
WHERE n.nspname = 'public' AND c.contype = 'c' AND c.conrelid::regclass::text = ANY(:tables)
ORDER BY 1, 2
"""
# The four 0002/0005/0007 checks carry the naming convention's double prefix in the database;
# official_win_payouts (0020, op.f) is literal. 0023 must keep exactly these names.
_WIDENED = {
    ("ingestion_jobs", "ck_ingestion_jobs_ck_ingestion_jobs_source"),
    ("id_mappings", "ck_id_mappings_ck_id_mappings_source"),
    ("exotic_odds", "ck_exotic_odds_ck_exotic_odds_source"),
    ("race_laps", "ck_race_laps_ck_race_laps_source"),
    ("official_win_payouts", "ck_official_win_payouts_source"),
}


def _snapshot(conn):
    tables = [r[0] for r in conn.execute(text(_EXISTING_TABLES_SQL))]
    cols = conn.execute(text(_COLUMNS_SQL), {"tables": tables}).fetchall()
    checks = conn.execute(text(_CHECKS_SQL), {"tables": tables}).fetchall()
    return set(tables), {tuple(r) for r in cols}, {(r[0], r[1]): r[2] for r in checks}


def test_0023_0024_change_only_the_allowed_schema(alembic_cfg, database_url, _migrated):
    eng = create_engine(database_url)
    try:
        command.downgrade(alembic_cfg, "0022_decision_quotes")
        with eng.connect() as conn:
            t0, cols0, checks0 = _snapshot(conn)
            assert "race_field_provenance" not in {
                r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables"))
            }
        command.upgrade(alembic_cfg, "head")
        with eng.connect() as conn:
            t1, cols1, checks1 = _snapshot(conn)
            new = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables"))}
        assert t0 == t1
        assert cols0 == cols1, "existing column definitions must not change"
        changed = {k for k in checks0 if checks0[k] != checks1.get(k)}
        assert changed == _WIDENED, changed
        assert set(checks0) == set(checks1), "no CHECK added or removed on existing tables"
        for k in _WIDENED:
            assert "jra_official" in checks1[k]
        assert {
            "race_horse_field_provenance",
            "race_field_provenance",
            "jra_track_conditions",
        } <= new
        # round trip again for idempotency
        command.downgrade(alembic_cfg, "0022_decision_quotes")
        command.upgrade(alembic_cfg, "head")
    finally:
        eng.dispose()


def test_new_tables_enforce_their_checks(engine):
    now = datetime.datetime.now(datetime.UTC)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO races (race_id, race_date) VALUES ('202605041101', '2026-11-01')")
        )
        with pytest.raises(IntegrityError):  # unknown group
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO race_field_provenance "
                        "(race_id, field_group, source, observed_at) "
                        "VALUES ('202605041101', 'owner', 'jra_official', :now)"
                    ),
                    {"now": now},
                )
        with pytest.raises(IntegrityError):  # official_at only on odds groups
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO race_field_provenance "
                        "(race_id, field_group, source, official_at, observed_at) "
                        "VALUES ('202605041101', 'results', 'jra_official', :now, :now)"
                    ),
                    {"now": now},
                )
        conn.execute(
            text(
                "INSERT INTO race_field_provenance "
                "(race_id, field_group, source, official_at, observed_at) "
                "VALUES ('202605041101', 'win_odds', 'jra_official', :now, :now)"
            ),
            {"now": now},
        )
        with pytest.raises(IntegrityError):  # measured_at after fetched_at
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO jra_track_conditions (venue_code, measured_at, item, "
                        "time_basis, value_num, fetched_at, raw_sha256) "
                        "VALUES ('05', :later, 'cushion_turf', 'measured', 9.6, :now, 'x')"
                    ),
                    {"now": now, "later": now + datetime.timedelta(hours=1)},
                )
        conn.execute(
            text(
                "INSERT INTO jra_track_conditions (venue_code, measured_at, item, time_basis, "
                "value_num, fetched_at, raw_sha256) "
                "VALUES ('05', :now, 'cushion_turf', 'measured', 9.6, :now, 'x')"
            ),
            {"now": now},
        )


def test_downgrade_refused_once_jra_rows_exist(alembic_cfg, database_url, _migrated):
    eng = create_engine(database_url)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ingestion_jobs (source, job_type, status) "
                    "VALUES ('jra_official', 'jra_entries', 'succeeded')"
                )
            )
        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(alembic_cfg, "0022_decision_quotes")
        with eng.begin() as conn:  # still at head, table intact
            conn.execute(text("SELECT 1 FROM race_field_provenance LIMIT 1"))
            conn.execute(text("DELETE FROM ingestion_jobs WHERE source = 'jra_official'"))
            conn.execute(
                text(
                    "INSERT INTO jra_track_conditions (venue_code, measured_at, item, time_basis, "
                    "value_num, fetched_at, raw_sha256) "
                    "VALUES ('05', now(), 'cushion_turf', 'measured', 9.6, now(), 'x')"
                )
            )
        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(alembic_cfg, "0023_jra_official_source")
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM jra_track_conditions"))
        command.downgrade(alembic_cfg, "0022_decision_quotes")
        command.upgrade(alembic_cfg, "head")
    finally:
        eng.dispose()
