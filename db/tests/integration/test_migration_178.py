"""178 T018: migration 0025 (jrdb source + jrdb_files).

SC-008: existing tables keep every column definition; the only differences between 0024 and 0025
are the seven widened source CHECKs and the one new table. Downgrade refuses once JRDB rows exist.
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
WHERE table_schema = 'public' AND table_name NOT IN ('alembic_version', 'jrdb_files')
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
_WIDENED = {
    ("ingestion_jobs", "ck_ingestion_jobs_ck_ingestion_jobs_source"),
    ("id_mappings", "ck_id_mappings_ck_id_mappings_source"),
    ("exotic_odds", "ck_exotic_odds_ck_exotic_odds_source"),
    ("race_laps", "ck_race_laps_ck_race_laps_source"),
    ("official_win_payouts", "ck_official_win_payouts_source"),
    ("race_horse_field_provenance", "ck_race_horse_field_provenance_source"),
    ("race_field_provenance", "ck_race_field_provenance_source"),
}
_JRDB_FILE_CHECKS_SQL = """
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE conrelid = 'jrdb_files'::regclass ORDER BY conname
"""


def _snapshot(conn):
    tables = [r[0] for r in conn.execute(text(_EXISTING_TABLES_SQL))]
    cols = conn.execute(text(_COLUMNS_SQL), {"tables": tables}).fetchall()
    checks = conn.execute(text(_CHECKS_SQL), {"tables": tables}).fetchall()
    return set(tables), {tuple(r) for r in cols}, {(r[0], r[1]): r[2] for r in checks}


def test_0025_changes_only_the_allowed_schema(alembic_cfg, database_url, _migrated):
    eng = create_engine(database_url)
    try:
        command.downgrade(alembic_cfg, "0024_jra_track_conditions")
        with eng.connect() as conn:
            t0, cols0, checks0 = _snapshot(conn)
            assert "jrdb_files" not in {
                r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables"))
            }
        command.upgrade(alembic_cfg, "head")
        with eng.connect() as conn:
            t1, cols1, checks1 = _snapshot(conn)
            new = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables"))}
            jrdb_checks = {r[0]: r[1] for r in conn.execute(text(_JRDB_FILE_CHECKS_SQL))}
        assert t0 == t1
        assert cols0 == cols1, "existing column definitions must not change"
        changed = {k for k in checks0 if checks0[k] != checks1.get(k)}
        assert changed == _WIDENED, changed
        assert set(checks0) == set(checks1), "no CHECK added or removed on existing tables"
        for k in _WIDENED:
            assert "jrdb" in checks1[k]
        assert "jrdb_files" in new
        # the new table's constraints match data-model §2
        assert "ck_jrdb_files_kind" in jrdb_checks and "'KYI'" in jrdb_checks["ck_jrdb_files_kind"]
        assert (
            "ck_jrdb_files_variant" in jrdb_checks
            and "'venue_l'" in jrdb_checks["ck_jrdb_files_variant"]
        )
        assert (
            "ck_jrdb_files_status" in jrdb_checks
            and "'held'" in jrdb_checks["ck_jrdb_files_status"]
        )
        assert "uq_jrdb_files_kind_date_variant_sha" in jrdb_checks  # UNIQUE shows as a constraint
        # round trip again for idempotency
        command.downgrade(alembic_cfg, "0024_jra_track_conditions")
        command.upgrade(alembic_cfg, "head")
    finally:
        eng.dispose()


def test_jrdb_files_enforces_its_checks_and_uniqueness(engine):
    now = datetime.datetime.now(datetime.UTC)
    base = (
        "INSERT INTO jrdb_files (kind, data_date, variant, sha256, size_bytes, record_length, "
        "source_url_sha256, fetched_at{extra_cols}) VALUES "
        "(:kind, '2008-09-13', :variant, :sha, 10, 376, 'u', :now{extra_vals})"
    )
    plain = base.format(extra_cols="", extra_vals="")
    with engine.begin() as conn:
        conn.execute(text(plain), {"kind": "SED", "variant": "daily", "sha": "a", "now": now})
        with pytest.raises(IntegrityError):  # same content twice
            with conn.begin_nested():
                conn.execute(
                    text(plain), {"kind": "SED", "variant": "daily", "sha": "a", "now": now}
                )
        # a differing-content version of the same file is a second row
        conn.execute(text(plain), {"kind": "SED", "variant": "daily", "sha": "b", "now": now})
        with pytest.raises(IntegrityError):  # unknown kind
            with conn.begin_nested():
                conn.execute(
                    text(plain), {"kind": "XYZ", "variant": "daily", "sha": "c", "now": now}
                )
        with pytest.raises(IntegrityError):  # unknown variant
            with conn.begin_nested():
                conn.execute(
                    text(plain), {"kind": "SED", "variant": "weekly", "sha": "d", "now": now}
                )
        with pytest.raises(IntegrityError):  # unknown status
            with conn.begin_nested():
                conn.execute(
                    text(base.format(extra_cols=", status", extra_vals=", 'done'")),
                    {"kind": "SED", "variant": "daily", "sha": "e", "now": now},
                )
        row = conn.execute(
            text("SELECT status, race_status, applied_at FROM jrdb_files WHERE sha256 = 'a'")
        ).one()
        assert row == ("fetched", {}, None)  # defaults


def test_widened_checks_accept_jrdb_rows(engine):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ingestion_jobs (source, job_type, status) "
                "VALUES ('jrdb', 'jrdb_entries', 'succeeded')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO id_mappings (entity_type, source, source_id, mapping_status) "
                "VALUES ('jockey', 'jrdb', '10076', 'unmapped')"
            )
        )
        conn.execute(
            text("INSERT INTO races (race_id, race_date) VALUES ('200801020101', '2008-09-13')")
        )
        conn.execute(
            text(
                "INSERT INTO official_win_payouts "
                "(race_id, horse_number, payout_yen, source, observed_at) "
                "VALUES ('200801020101', 1, 880, 'jrdb_result', now())"
            )
        )
        with pytest.raises(IntegrityError):  # the page-kind check does not take the feed name
            with conn.begin_nested():
                conn.execute(
                    text(
                        "INSERT INTO official_win_payouts "
                        "(race_id, horse_number, payout_yen, source, observed_at) "
                        "VALUES ('200801020101', 2, 100, 'jrdb', now())"
                    )
                )


def test_downgrade_refused_once_jrdb_rows_exist(alembic_cfg, database_url, _migrated):
    eng = create_engine(database_url)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ingestion_jobs (source, job_type, status) "
                    "VALUES ('jrdb', 'jrdb_fetch', 'succeeded')"
                )
            )
        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(alembic_cfg, "0024_jra_track_conditions")
        with eng.begin() as conn:  # still at head, table intact
            conn.execute(text("SELECT 1 FROM jrdb_files LIMIT 1"))
            conn.execute(text("DELETE FROM ingestion_jobs WHERE source = 'jrdb'"))
            conn.execute(
                text(
                    "INSERT INTO jrdb_files (kind, data_date, variant, sha256, size_bytes, "
                    "record_length, source_url_sha256, fetched_at) "
                    "VALUES ('KYI', '2008-09-13', 'daily', 'z', 1, 1024, 'u', now())"
                )
            )
        with pytest.raises(RuntimeError, match="downgrade refused"):  # a file row alone refuses too
            command.downgrade(alembic_cfg, "0024_jra_track_conditions")
        with eng.begin() as conn:
            conn.execute(text("DELETE FROM jrdb_files"))
        command.downgrade(alembic_cfg, "0024_jra_track_conditions")
        command.upgrade(alembic_cfg, "head")
    finally:
        eng.dispose()
