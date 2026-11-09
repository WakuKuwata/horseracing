"""178 T073: a differing KYI arriving after the applied one is ``held`` (INV-F2) and changes no
product value; ``jrdb-fetch --show-held`` shows the row diff; ``jrdb-apply-held`` without
``--yes`` only shows it (exit 2, nothing written); with ``--yes`` the current version becomes
``superseded``, the held file ``applied``, and the writer's rules decide what moves: the
取消 flag (a one-way transition) lands, first-writer items (distance, the other horses) stay."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
from pathlib import Path

import pytest
from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race, RaceHorse
from horseracing_db.models.jrdb import JrdbFile
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from horseracing_scrape.jrdb import held as H
from horseracing_scrape.jrdb.cli import ingest_archive_bytes, run_apply_held, run_show_held
from horseracing_scrape.jrdb.cli_write import run_write
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from horseracing_scrape.upsert import Counts
from tests._jrdb_synth import (
    DAY,
    JST,
    YEAR,
    public_sample,
    shift_raw,
    shifted_results,
    synth_kyi_raw,
    synth_ukc_raw,
    table_snapshot,
)

pytestmark = pytest.mark.integration
T0 = datetime.datetime(2026, 9, 12, 20, 0, tzinfo=JST)
T1 = T0 + datetime.timedelta(hours=2)


def _ingest(session, artifacts: Path, name: str, raw: bytes, *, fetched_at) -> Counts:
    counts = Counts()
    ingest_archive_bytes(
        session,
        raw=raw,
        name=name,
        variant="daily",
        url="u",
        fetched_at=fetched_at,
        published_at=None,
        artifacts_dir=artifacts,
        counts=counts,
        files_summary=[],
    )
    session.commit()
    return counts


def _write_args(policy: Path, artifacts: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command="jrdb-entries",
        date=DAY.isoformat(),
        profile="current",
        venue=None,
        database_url=None,
        policy=str(policy),
        artifacts_dir=str(artifacts),
        json=True,
    )


def _apply_args(
    file_id: str, policy: Path, *, yes: bool, replaces: str | None = None
) -> argparse.Namespace:
    return argparse.Namespace(
        command="jrdb-apply-held",
        file_id=file_id,
        profile="current",
        yes=yes,
        replaces=replaces,
        database_url=None,
        policy=str(policy),
        artifacts_dir=None,
        json=True,
    )


def _snapshot(session):
    return {
        t: table_snapshot(session, t, pk)
        for t, pk in (
            ("races", ("race_id",)),
            ("race_horses", ("race_id", "horse_id")),
            ("horses", ("horse_id",)),
        )
    }


def test_held_file_changes_nothing_until_the_operator_approves_it(engine, session, tmp_path):
    artifacts = tmp_path / "artifacts"
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"jrdb_enabled": True}), encoding="utf-8")
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    results = shifted_results(races=2)
    day6 = DAY.strftime("%y%m%d")

    # 1) the day's files arrive and are applied through the writer command
    bac = shift_raw("BAC", public_sample("BAC").read_bytes(), year_from=2008, year_to=YEAR)
    _ingest(session, artifacts, f"BAC{day6}.txt", bac, fetched_at=T0)
    _ingest(session, artifacts, f"KYI{day6}.txt", synth_kyi_raw(results), fetched_at=T0)
    _ingest(
        session, artifacts, f"UKC{day6}.txt", synth_ukc_raw(results, race_year=YEAR), fetched_at=T0
    )
    wr = run_write(
        _write_args(policy, artifacts), session_factory=sf, artifacts=artifacts, production_url=None
    )
    assert wr.exit_code == 0, wr
    session.expire_all()
    kyi_a = session.scalar(select(JrdbFile).where(JrdbFile.kind == "KYI"))
    assert kyi_a.status == "applied"
    rids = sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})
    assert session.scalar(select(Race.distance).where(Race.race_id == rids[0])) is not None
    before = _snapshot(session)

    # 2) a differing KYI (one horse now 取消) arrives later: held, nothing written
    victim = next(r for r in results if race_id_from_key(r.key, file_year=YEAR) == rids[0])
    results_b = [dataclasses.replace(r, abnormal_code=1) if r is victim else r for r in results]
    c = _ingest(session, artifacts, f"KYI{day6}.txt", synth_kyi_raw(results_b), fetched_at=T1)
    assert c.extra.get("held") == 1
    session.expire_all()
    kyi_b = session.scalar(select(JrdbFile).where(JrdbFile.status == "held"))
    assert kyi_b is not None and kyi_b.kind == "KYI" and kyi_b.sha256 != kyi_a.sha256
    assert len(list((artifacts / "KYI" / DAY.strftime("%Y%m%d")).glob("*.txt"))) == 2
    wr2 = run_write(
        _write_args(policy, artifacts), session_factory=sf, artifacts=artifacts, production_url=None
    )
    assert any(n.startswith("held:") for n in wr2.notes), wr2.notes
    session.expire_all()
    assert _snapshot(session) == before  # values AND xmin: not even an unneeded write

    # 3) --show-held: the diff names the one changed row and field
    shown = run_show_held(
        argparse.Namespace(date=None), session_factory=sf, artifacts_dir=artifacts
    )
    assert shown.exit_code == 0 and len(shown.held) == 1
    desc = shown.held[0]
    assert desc["held"]["jrdb_file_id"] == str(kyi_b.jrdb_file_id)
    assert desc["current"]["jrdb_file_id"] == str(kyi_a.jrdb_file_id)
    assert desc["reapply_command"] == "jrdb-entries" and desc["error"] is None
    diff = desc["diff"]
    assert diff["n_changed"] == 1 and diff["n_only_in_held"] == diff["n_only_in_current"] == 0
    assert diff["changed"][0]["key"].endswith(f"#{victim.horse_number:02d}")
    assert diff["changed"][0]["fields"] == {"cancelled": [False, True]}
    assert diff["changed_fields_count"] == {"cancelled": 1} and diff["truncated"] is False
    assert diff["duplicate_keys_held"] == 0 and diff["rejected_rows_held"] == 0
    assert desc["decode_profile"] == "current" and desc["pending"] is None and desc["note"]

    # 4) apply-held without --yes: shows, refuses, writes nothing
    res = run_apply_held(
        _apply_args(str(kyi_b.jrdb_file_id), policy, yes=False),
        session_factory=sf,
        artifacts_dir=artifacts,
        production_url=None,
    )
    assert res.exit_code == 2 and res.error.startswith("confirmation_required")
    assert res.held[0]["diff"]["n_changed"] == 1 and res.approval is None
    session.expire_all()
    assert session.get(JrdbFile, kyi_b.jrdb_file_id).status == "held"
    assert _snapshot(session) == before

    # 5) --yes must name the version it replaces (codex D26 #5): missing or stale → refused
    for replaces, code in (
        (None, "replaces_required"),
        (str(kyi_b.jrdb_file_id), "current_changed"),
    ):
        res = run_apply_held(
            _apply_args(str(kyi_b.jrdb_file_id), policy, yes=True, replaces=replaces),
            session_factory=sf,
            artifacts_dir=artifacts,
            production_url=None,
        )
        assert res.exit_code == 2 and res.error.startswith(code), res.error
        session.expire_all()
        assert session.get(JrdbFile, kyi_b.jrdb_file_id).status == "held"
        assert _snapshot(session) == before

    # 6) --yes --replaces <current>: the current version is superseded, the held file applied,
    #    the 取消 lands, first-writer items and the other horses are untouched
    res = run_apply_held(
        _apply_args(str(kyi_b.jrdb_file_id), policy, yes=True, replaces=str(kyi_a.jrdb_file_id)),
        session_factory=sf,
        artifacts_dir=artifacts,
        production_url=None,
    )
    assert res.exit_code == 0, res
    assert res.approval["superseded"] == str(kyi_a.jrdb_file_id)
    assert res.approval["reopened_driver_rows"] == []
    assert res.jobs and res.jobs[0]["status"] == "succeeded"
    session.expire_all()
    assert session.get(JrdbFile, kyi_a.jrdb_file_id).status == "superseded"
    row_b = session.get(JrdbFile, kyi_b.jrdb_file_id)
    assert row_b.status == "applied" and row_b.applied_at is not None
    hid = horse_id_from_blood_no(victim.blood_reg_no, race_year=YEAR)
    rh = session.scalar(
        select(RaceHorse).where(RaceHorse.race_id == rids[0], RaceHorse.horse_id == hid)
    )
    assert rh.entry_status == EntryStatus.CANCELLED
    after = _snapshot(session)
    # first-writer items are kept: every value of races / horses is as the first file wrote it,
    # and of race_horses every row but the 取消 one (the re-run is an ordinary writer pass, so
    # xmin may move where the common writer upserts identical values — measured in
    # test_jrdb_replay_order; here the product VALUES are the contract)
    assert after["races"][0] == before["races"][0]
    assert after["horses"][0] == before["horses"][0]
    vals_before, vals_after = before["race_horses"][0], after["race_horses"][0]
    key = (rids[0], hid)
    assert {k: v for k, v in vals_after.items() if k != key} == {
        k: v for k, v in vals_before.items() if k != key
    }
    assert vals_before[key]["entry_status"] != vals_after[key]["entry_status"]
    # nothing held any more
    assert H.list_held(session) == []
    assert run_show_held(
        argparse.Namespace(date=DAY.isoformat()), session_factory=sf, artifacts_dir=artifacts
    ).notes == [f"no held files for {DAY}"]


def test_apply_held_refuses_rows_that_are_not_held(engine, session, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"jrdb_enabled": True}), encoding="utf-8")
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    res = run_apply_held(
        _apply_args("not-a-uuid", policy, yes=True),
        session_factory=sf,
        artifacts_dir=tmp_path,
        production_url=None,
    )
    assert res.exit_code == 2 and res.error.startswith("held_not_found")
    # policy off: refused before anything is read
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"jrdb_enabled": False}), encoding="utf-8")
    res = run_apply_held(
        _apply_args("00000000-0000-0000-0000-000000000000", off, yes=True),
        session_factory=sf,
        artifacts_dir=tmp_path,
        production_url=None,
    )
    assert res.exit_code == 2 and res.error.startswith("jrdb_disabled")


def test_a_second_approval_waits_for_the_writer_and_two_active_versions_stop_the_path(
    session, tmp_path
):
    """codex D26 #2/#4/#8: B approved but not yet applied blocks approving C; two active
    versions of one key are an INV-F1 violation, not something to order away; a held row whose
    text is not on disk cannot be approved."""

    import datetime as dt

    from horseracing_scrape.jrdb import files as jfiles
    from horseracing_scrape.jrdb.models import JrdbFileMeta

    def meta(sha):
        return JrdbFileMeta("KYI", "11", DAY, "daily", sha, 10, 1024)

    a, _ = jfiles.record_fetched(
        session, meta("a" * 64), source_url_sha256="u", fetched_at=T0, published_at=None
    )
    a.status = "applied"
    b, _ = jfiles.record_fetched(
        session, meta("b" * 64), source_url_sha256="u", fetched_at=T1, published_at=None
    )
    jfiles.mark_held(session, b)
    c, _ = jfiles.record_fetched(
        session, meta("c" * 64), source_url_sha256="u", fetched_at=T1, published_at=None
    )
    jfiles.mark_held(session, c)
    session.commit()
    now = dt.datetime.now(dt.UTC)
    # no text on disk → describe carries an error → the CLI refuses (held_diff_incomplete)
    desc = H.describe(session, tmp_path, b)
    assert (
        desc["error"]
        and desc["diff"] is None
        and desc["current"]["jrdb_file_id"] == str(a.jrdb_file_id)
    )
    # approve B (the model function; the CLI would have run the writer next)
    out = H.approve(session, b, now=now, replaces=str(a.jrdb_file_id))
    session.commit()
    assert out["superseded"] == str(a.jrdb_file_id) and b.status == "fetched"
    # C cannot be approved while B is pending
    with pytest.raises(H.HeldNotApprovable) as exc:
        H.approve(session, c, now=now, replaces="none")
    assert exc.value.code == "pending_version"
    assert H.describe(session, tmp_path, c)["pending"]["jrdb_file_id"] == str(b.jrdb_file_id)
    # two active versions (someone broke INV-F1): the path stops instead of picking one
    b.status = "applied"
    a.status = "applied"
    session.flush()
    with pytest.raises(H.HeldNotApprovable) as exc:
        H.current_version(session, c)
    assert exc.value.code == "inv_f1_violation"
    assert H.describe(session, tmp_path, c)["error"].startswith("inv_f1_violation")
    session.rollback()
