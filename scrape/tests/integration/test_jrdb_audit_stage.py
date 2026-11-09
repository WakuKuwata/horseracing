"""178 T070: ``jrdb-audit`` の「段の状態」— the day's JRDB files, the route that created the rows,
the T−3 records by source, results / final odds / payouts coverage, the policy switches, and the
'stopped' hints an operator reads before choosing emergency mode or re-enabling netkeiba."""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb.cli_write import run_audit, stage_status
from tests._jrdb_synth import (
    DAY,
    JST,
    YEAR,
    entries_from_results,
    horses_from_results,
    shifted_programs,
    shifted_results,
)

pytestmark = pytest.mark.integration
OBS = datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)


def test_empty_day_says_entries_are_the_stopped_stage(session, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {"netkeiba_enabled": False, "jrdb_enabled": True, "near_post_source": "jra_official"}
        ),
        encoding="utf-8",
    )
    st = stage_status(session, DAY, policy)
    assert st["races"] == 0 and st["jrdb_files_missing"] == list(
        ("BAC", "KYI", "UKC", "TYB", "HJC", "SED", "SRB")
    )
    flags = {k: v for k, v in st["policy"].items() if k not in ("path", "sha256")}
    assert flags == {
        "netkeiba_enabled": False,
        "jrdb_enabled": True,
        "jra_emergency_entries": False,
        "near_post_source": "jra_official",
    }
    assert st["expected_races"] is None
    assert st["stopped"] and st["stopped"][0].startswith("undetermined:")  # no KYI, no rows
    assert st["policy"]["path"] == str(policy) and len(st["policy"]["sha256"]) == 64


def test_a_day_with_rows_shows_the_route_and_what_is_still_missing(engine, session, tmp_path):
    results = shifted_results(races=2)
    W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=OBS,
        job_id=None,
        person_map={},
        race_status=W.RaceStatus(),
    )
    session.commit()
    st = stage_status(session, DAY, None)  # the committed policy: defaults
    assert st["races"] == 2 and st["races_with_post_time"] == 2
    assert st["expected_races"] is None  # the writer was called directly: no KYI file row
    assert st["entries_by_source"] == {"jrdb": 2} and st["decision_quotes_by_source"] == {}
    assert st["results_races"] == 0 and st["payout_races"] == 0
    assert (
        st["policy"]["netkeiba_enabled"] is True and st["policy"]["near_post_source"] == "netkeiba"
    )
    assert [s.split(":")[0] for s in st["stopped"]] == [
        "near_post",
        "results",
        "final_odds",
        "payouts",
    ]
    # through the CLI function: READ ONLY (nothing written by the audit)
    before = session.execute(text("SELECT count(*) FROM ingestion_jobs")).scalar_one()
    args = SimpleNamespace(
        command="jrdb-audit", date=DAY.isoformat(), policy=None, json=True, artifacts_dir=None
    )
    res = run_audit(
        args, session_factory=sessionmaker(bind=engine, expire_on_commit=False), artifacts=tmp_path
    )
    assert res.audit["stage"]["races"] == 2 and res.exit_code == 0
    assert session.execute(text("SELECT count(*) FROM ingestion_jobs")).scalar_one() == before
