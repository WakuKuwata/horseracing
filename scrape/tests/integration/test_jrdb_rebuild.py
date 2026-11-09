"""178 T056: 段 3b — the 2008-09-13 public day rebuilt into a SECOND database (same alembic head)
under ``profile=jrdb_canonical``: product rows land there with the JRDB-defined columns filled in
JRDB's definition, the reference DB is only read, ``compare-2008.json`` carries both counts, a year
where JRDB falls short of the reference blocks (exit 1), and the same target as reference/env is
refused."""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from horseracing_scrape.jrdb.errors import JrdbStartupRefused
from horseracing_scrape.jrdb.rebuild import assert_distinct_targets, rebuild_range
from tests._jrdb_synth import write_public_day_archive
from tests.conftest import DB_DIR

pytestmark = pytest.mark.integration
D0, D1 = datetime.date(2008, 1, 1), datetime.date(2008, 12, 31)
TABLES = (
    "races",
    "race_horses",
    "horses",
    "race_results",
    "official_win_payouts",
    "exotic_odds",
    "race_laps",
    "jrdb_files",
    "ingestion_jobs",
)


@pytest.fixture(scope="module")
def canonical_db_url():
    """A second migrated PostgreSQL: the JRDB canonical DB of 段 3b."""

    with PostgresContainer("postgres:16", driver="psycopg") as container:
        url = container.get_connection_url()
        prev = os.environ.get("DATABASE_URL")
        cfg = Config(str(DB_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(DB_DIR / "migrations"))
        os.environ["DATABASE_URL"] = url
        try:
            command.upgrade(cfg, "head")
        finally:
            if prev is not None:
                os.environ["DATABASE_URL"] = prev
        yield url


def _counts(url: str) -> dict[str, int]:
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return {
                t: int(c.execute(text(f"SELECT count(*) FROM {t}")).scalar_one()) for t in TABLES
            }
    finally:
        eng.dispose()


def _q(url: str, sql: str):
    eng = create_engine(url)
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def test_rebuild_writes_the_canonical_db_and_only_reads_the_reference(
    engine, database_url, canonical_db_url, tmp_path
):
    files = write_public_day_archive(tmp_path / "files")
    assert set(files) == {"BAC", "SED", "SRB", "KYI", "UKC", "HJC"}
    ref_before = _counts(database_url)
    out_dir = tmp_path / "out"
    rep = rebuild_range(
        tmp_path / "files",
        D0,
        D1,
        db_url=canonical_db_url,
        reference_db_url=database_url,
        out_dir=out_dir,
        env_url=database_url,
    )
    assert rep.error is None and rep.exit_code == 0 and rep.blocking_years == []
    assert rep.schema_head and len(rep.years) == 1
    y = rep.years[0]
    assert y.year == 2008 and y.days == 1
    assert [j["step"] for j in y.jobs] == ["entries", "prerace", "results", "payouts"][:1] + [
        j["step"] for j in y.jobs[1:]
    ]
    assert {j["status"] for j in y.jobs} <= {"succeeded", "partial"}
    assert y.counts_jrdb["races"] == 24 and y.counts_jrdb["entries"] == 324
    assert y.counts_jrdb["results"] > 300 and y.counts_jrdb["payout_rows"] == 24
    assert y.counts_jrdb["laps"] == 23  # the sample's 中山 4R (障害) carries no lap times
    assert y.counts_current == {k: 0 for k in y.counts_current}
    # the JRDB-defined columns are filled in JRDB's definition (FR-021), for every distance
    fill = y.fill_jrdb
    assert fill["race_results.first_3f"]["rate"] > 0.95
    assert fill["race_results.corner_orders"]["rate"] > 0.9
    assert fill["race_horses.running_style"]["rate"] > 0.9
    assert fill["horses.sire_line"]["rate"] == 1.0 and fill["horses.damsire_line"]["rate"] == 1.0
    assert fill["horses.owner_name"]["rate"] == 1.0 and fill["races.post_time"]["rate"] == 1.0
    # 2008 final odds landed although it is a pre-netkeiba year (the canonical DB has no era guard)
    assert fill["race_horses.odds"]["rate"] > 0.9
    rows = _q(
        canonical_db_url,
        "SELECT r.distance, rr.first_3f, rr.corner_orders, rh.running_style FROM race_results rr "
        "JOIN race_horses rh USING (race_id, horse_id) JOIN races r USING (race_id) "
        "WHERE rr.finish_order = 1 ORDER BY rr.race_id",
    )
    assert any(d != 1200 and f3 is not None for d, f3, _c, _s in rows)
    assert all(
        src == "jrdb" for (src,) in _q(canonical_db_url, "SELECT DISTINCT data_source FROM horses")
    )
    assert all(
        src == "jrdb"
        for (src,) in _q(canonical_db_url, "SELECT DISTINCT source FROM race_field_provenance")
    )
    # the reference was only read
    assert _counts(database_url) == ref_before
    # the comparison file
    doc = json.loads((out_dir / "compare-2008.json").read_text(encoding="utf-8"))
    assert doc["counts"]["races"] == {"jrdb": 24, "current": 0} and doc["blocking"] == []
    assert all(v["n"] == 0 for v in doc["missing_in_jrdb"].values())
    assert rep.report_paths == [str(out_dir / "compare-2008.json")]
    # jrdb_files rows exist for every archive file, all applied; the job summaries point at them
    files_rows = _q(canonical_db_url, "SELECT kind, variant, status FROM jrdb_files ORDER BY kind")
    assert {k for k, _v, _s in files_rows} == {"BAC", "HJC", "KYI", "SED", "SRB", "UKC"}
    assert all(v == "archive" for _k, v, _s in files_rows)
    assert {s for _k, _v, s in files_rows} <= {"applied", "partially_applied", "fetched"}
    (summary,) = _q(
        canonical_db_url,
        "SELECT summary FROM ingestion_jobs WHERE job_type = 'jrdb_rebuild_results'",
    )[0]
    assert all(f["jrdb_file_id"] for f in summary["files"]) and {
        f["kind"] for f in summary["files"]
    } == {"SED", "SRB"}

    # a second run of the same files is a no-op on product values (applied races are skipped)
    before = _q(
        canonical_db_url,
        "SELECT race_id, horse_id, odds, xmin::text FROM race_horses ORDER BY 1, 2",
    )
    rep2 = rebuild_range(
        tmp_path / "files",
        D0,
        D1,
        db_url=canonical_db_url,
        reference_db_url=database_url,
        out_dir=tmp_path / "out2",
        env_url=database_url,
    )
    assert rep2.exit_code == 0
    assert before == _q(
        canonical_db_url,
        "SELECT race_id, horse_id, odds, xmin::text FROM race_horses ORDER BY 1, 2",
    )

    # a year where the reference has MORE blocks (FR-020)
    with engine.begin() as conn:
        for i in range(30):  # race_number is CHECKed to 1..12: spread over three venues
            venue, n = ("05", "06", "09")[i // 10], i % 10 + 1
            conn.execute(
                text(
                    "INSERT INTO races (race_id, race_date, race_number, venue_code) "
                    "VALUES (:r, '2008-09-14', :n, :v)"
                ),
                {"r": f"2008{venue}0501{n:02d}", "n": n, "v": venue},  # 5回: no JRDB id
            )
    rep3 = rebuild_range(
        tmp_path / "files",
        D0,
        D1,
        db_url=canonical_db_url,
        reference_db_url=database_url,
        out_dir=tmp_path / "out3",
        env_url=database_url,
    )
    assert rep3.exit_code == 1 and rep3.blocking_years == [2008] and "FR-020" in rep3.error
    assert rep3.years[0].blocking == ["races"]
    doc3 = json.loads((tmp_path / "out3" / "compare-2008.json").read_text(encoding="utf-8"))
    assert doc3["counts"]["races"] == {"jrdb": 24, "current": 30}
    miss = doc3["missing_in_jrdb"]["races"]
    assert miss["n"] == 30 and miss["surplus_in_jrdb"] == 24 and len(miss["sample"]) == 20
    assert doc3["compared_at"]


def test_same_database_through_another_url_spelling_is_refused(database_url, tmp_path):
    """codex D23 Q2 #1: the URL strings differ, the connected database is the same."""

    alias = database_url + ("&" if "?" in database_url else "?") + "application_name=alias"
    rep = rebuild_range(
        tmp_path,
        D0,
        D1,
        db_url=alias,
        reference_db_url=database_url,
        out_dir=tmp_path / "o",
        env_url=None,
    )
    assert rep.exit_code == 2 and rep.error.startswith("same_database")


def test_a_target_holding_another_routes_rows_is_refused(
    engine, database_url, canonical_db_url, tmp_path
):
    """codex D23 Q2 #5: the canonical DB must be JRDB-only (FR-021)."""

    with engine.begin() as conn:  # the REFERENCE gets a netkeiba row → use it as the target
        conn.execute(
            text(
                "INSERT INTO horses (horse_id, horse_name, data_source) "
                "VALUES ('2020000001', 'x', 'netkeiba')"
            )
        )
    rep = rebuild_range(
        tmp_path,
        D0,
        D1,
        db_url=database_url,
        reference_db_url=canonical_db_url,
        out_dir=tmp_path / "o",
        env_url="",  # the process DATABASE_URL is this very DB; the subject here is the row check
    )
    assert rep.exit_code == 2 and rep.error.startswith("target_not_jrdb_only")
    assert "horses" in rep.error


def test_same_database_as_reference_or_env_is_refused(database_url, tmp_path):
    with pytest.raises(JrdbStartupRefused) as exc:
        assert_distinct_targets(database_url, database_url, None)
    assert exc.value.code == "same_database"
    with pytest.raises(JrdbStartupRefused):
        assert_distinct_targets(
            "postgresql+psycopg://x@h/other",
            "postgresql+psycopg://x@h/ref",
            "postgresql+psycopg://x@h/other",
        )
    rep = rebuild_range(
        tmp_path,
        D0,
        D1,
        db_url=database_url,
        reference_db_url=database_url,
        out_dir=tmp_path / "o",
        env_url=None,
    )
    assert rep.exit_code == 2 and rep.error.startswith("same_database")
    assert not (tmp_path / "o").exists()
    assert Path(tmp_path).exists()
