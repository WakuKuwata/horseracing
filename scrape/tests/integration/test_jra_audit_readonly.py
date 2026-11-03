"""176 T081: jra-audit reads saved raw, opens the DB READ ONLY and changes nothing."""

from __future__ import annotations

import argparse
import datetime

import pytest
from horseracing_db.models import Horse, RaceHorse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from horseracing_scrape.jra.audit import run_fetch
from horseracing_scrape.jra.cli import run_command
from horseracing_scrape.jra.parse.changes import parse_changes
from horseracing_scrape.jra.parse.entries import parse_entries_day
from horseracing_scrape.jra.upsert_jra import upsert_jra_entries
from tests._jra_fake import FakeJraFetcher
from tests._netkeiba_first import netkeiba_first
from tests.jra_fixtures import fetched_at, jra_text

pytestmark = pytest.mark.integration
D = datetime.date(2026, 11, 1)
_TABLES = (
    "races",
    "race_horses",
    "horses",
    "jockeys",
    "trainers",
    "race_results",
    "ingestion_jobs",
    "official_win_payouts",
    "exotic_odds",
    "race_field_provenance",
    "race_horse_field_provenance",
)


def _state(session):
    out = {}
    for t in _TABLES:
        out[t] = session.execute(text(f"SELECT count(*), max(updated_at) FROM {t}")).one()
    return out


def _seed(session):
    page = parse_entries_day(jra_text("entries_tokyo_20261101.sjis.html"), expected_date=D)
    changes = parse_changes(jra_text("changes_20261101.sjis.html"), expected_date=D)
    obs = fetched_at("entries_tokyo_20261101.sjis.html")
    # FR-011a: the netkeiba field first (canonical ids here), then the official values on it
    netkeiba_first(
        session, page, changes, observed_at=obs - datetime.timedelta(hours=10), alias=False
    )
    upsert_jra_entries(session, page, changes, observed_at=obs)
    session.commit()
    # one race keeps a surrogate for its 馬番 1 (an nk: twin of the official horse)
    rid = "202605041102"
    rh = next(r for r in session.query(RaceHorse).filter_by(race_id=rid, horse_number=1))
    official = rh.horse_id
    session.merge(Horse(horse_id=f"nk:{official}", horse_name="x", birth_year=2024))
    session.flush()
    session.execute(
        text("DELETE FROM race_horse_field_provenance WHERE race_id = :r AND horse_id = :o"),
        {"r": rid, "o": official},
    )
    session.execute(
        text("UPDATE race_horses SET horse_id = :nk WHERE race_id = :r AND horse_id = :o"),
        {"nk": f"nk:{official}", "r": rid, "o": official},
    )
    # and one stored jockey weight differs
    session.execute(
        text(
            "UPDATE race_horses SET jockey_weight = 99 WHERE race_id = '202605041103' "
            "AND horse_number = 2"
        )
    )
    session.commit()
    return official


def test_audit_compares_and_writes_nothing(session, session_factory, tmp_path):
    official = _seed(session)
    run_fetch(
        argparse.Namespace(save_dir=str(tmp_path / "raw"), kinds="entries", venue="05"),
        fetcher=FakeJraFetcher(),
        date=D,
    )
    before = _state(session)
    args = argparse.Namespace(from_dir=str(tmp_path / "raw"), json=True, dry_run=False)
    r = run_command("jra-audit", args, fetcher=None, session_factory=session_factory, date=D)
    session.expire_all()
    assert _state(session) == before
    assert r.exit_code == 0 and r.jobs == []
    fields = r.audit["fields"]
    jw = fields["jockey_weight"]
    assert jw["mismatched"] == 1 and jw["examples"][0]["race_id"] == "202605041103"
    assert jw["examples"][0]["stored_source"] == "jra_official"
    # FR-011a: the nk: twin of an official horse (no approved mapping) is compared as that horse
    assert not fields["jockey_id"]["excluded_by_reason"].get("unresolved_id")
    assert [f"nk:{official}", official] in r.audit["alias_matched"]
    assert [f"nk:{official}", official] in r.audit["merge_candidates"]
    assert fields["horse_id"]["comparable"] == fields["horse_id"]["matched"] > 100
    assert any(m["field"] == "jockey_weight" for m in r.mismatches)
    assert r.to_json()["audit"]["races"] == 12


def test_read_only_transaction_refuses_writes(session_factory):
    with session_factory() as s:
        s.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(DBAPIError):
            s.execute(text("UPDATE races SET race_name = 'x'"))
        s.rollback()


def test_manifest_for_another_day_is_refused(session_factory, tmp_path):
    run_fetch(
        argparse.Namespace(save_dir=str(tmp_path / "raw"), kinds="changes", venue=None),
        fetcher=FakeJraFetcher(),
        date=D,
    )
    args = argparse.Namespace(from_dir=str(tmp_path / "raw"), json=True, dry_run=False)
    r = run_command(
        "jra-audit",
        args,
        fetcher=None,
        session_factory=session_factory,
        date=D + datetime.timedelta(days=1),
    )
    assert r.exit_code == 2
