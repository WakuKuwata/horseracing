"""178 T055: 段 3a column repair on the current DB — value-changing rows only, other rows byte
stable (xmin), netkeiba-era spelling taken / JRA-VAN-era values kept / official-owned kept, the
dry run writes nothing, and the availability time of a repaired value is derivable from the
provenance job (data-model §3a) and survives a same-file re-run."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from horseracing_db.models import Horse, OfficialWinPayout, Race, RaceHorse
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from horseracing_scrape.jrdb import upsert_jrdb as W
from horseracing_scrape.jrdb.availability import available_at
from horseracing_scrape.jrdb.cli import run_repair
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from horseracing_scrape.jrdb.repair import (
    RepairColumnRefused,
    load_archive_files,
    repair_column,
)
from tests._jrdb_synth import (
    DAY,
    JST,
    YEAR,
    entries_from_results,
    horses_from_results,
    passing_parity_report,
    shifted_programs,
    shifted_results,
    table_snapshot,
    write_shifted_archive,
)

pytestmark = pytest.mark.integration
OBS = datetime.datetime(2026, 9, 18, 12, 0, tzinfo=JST)
D0, D1 = datetime.date(2024, 1, 1), datetime.date(2026, 12, 31)


@pytest.fixture
def archive(tmp_path) -> Path:
    d = tmp_path / "files"
    write_shifted_archive(d, {"BAC": 2026, "SED": 2026, "HJC": 2026, "UKC": 2026})
    write_shifted_archive(d / "y2024", {"BAC": 2024})
    return d


@pytest.fixture
def parity_dir(tmp_path) -> Path:
    d = tmp_path / "parity"
    d.mkdir()
    (d / "2008.json").write_text(json.dumps(passing_parity_report()), encoding="utf-8")
    return d


def _field(session, results=None):
    results = results or shifted_results()
    W.upsert_jrdb_entries(
        session,
        shifted_programs(results),
        entries_from_results(results),
        horses_from_results(results),
        profile="current",
        year=YEAR,
        observed_at=OBS - datetime.timedelta(days=5),
        job_id=None,
        person_map={},
        race_status=W.RaceStatus(),
    )
    session.commit()
    return results


def _unrelated_race(session):
    session.execute(
        text(
            "INSERT INTO races (race_id, race_date, race_number, venue_code) VALUES ('202605050101', '2026-09-14', 1, '05')"
        )
    )
    session.execute(
        text("INSERT INTO horses (horse_id, horse_name) VALUES ('2022000001', '無関係')")
    )
    session.execute(
        text(
            "INSERT INTO race_horses (race_id, horse_id, horse_number, entry_status, odds) "
            "VALUES ('202605050101', '2022000001', 1, 'started', 3.3)"
        )
    )
    session.commit()


def _repair(session, column, **kw):
    kw.setdefault("files_dir", kw.pop("archive"))
    rep = repair_column(session, column, D0, D1, dry_run=False, observed_at=OBS, **kw)
    session.commit()
    session.expire_all()
    return rep


# --------------------------------------------------------------------------- odds (SED 正式版)


def test_odds_updates_only_value_changing_rows_and_is_idempotent(session, archive, parity_dir):
    results = _field(session)
    _unrelated_race(session)
    before_other = table_snapshot(
        session, "race_horses", ("race_id", "horse_id"), "WHERE race_id = '202605050101'"
    )
    expected = sum(
        1 for r in results if r.final_win_odds is not None and r.abnormal_code not in (1, 2)
    )
    rep = _repair(session, "odds", archive=archive, parity_dir=parity_dir)
    assert rep.rows_changed == expected and rep.errors == []
    assert rep.races_seen == 24 and rep.skipped.get("race_not_in_db") is None
    assert rep.by_year_fill_before["2026"]["filled"] == 1  # only the unrelated race had odds
    assert rep.by_year_fill_after["2026"]["filled"] == expected + 1
    assert rep.parity_rates == {"odds": {"2008": 1.0}, "popularity": {"2008": 1.0}}
    rid = race_id_from_key(results[0].key, file_year=YEAR)
    hid = horse_id_from_blood_no(results[0].blood_reg_no, race_year=YEAR)
    rh = session.get(RaceHorse, (rid, hid))
    assert rh.odds == results[0].final_win_odds and rh.popularity == results[0].final_win_popularity
    race = session.get(Race, rid)
    assert race.win_odds_observed_at == OBS and race.win_odds_official_at is None  # 139/140 rule
    # the row outside the files did not move (values and xmin)
    assert before_other == table_snapshot(
        session, "race_horses", ("race_id", "horse_id"), "WHERE race_id = '202605050101'"
    )
    # same files again: nothing changes, no row is written
    before = table_snapshot(session, "race_horses", ("race_id", "horse_id"))
    rep2 = _repair(session, "odds", archive=archive, parity_dir=parity_dir)
    assert rep2.rows_changed == 0 and rep2.rows_unchanged == expected
    assert before == table_snapshot(session, "race_horses", ("race_id", "horse_id"))
    assert rep.files and rep.files[0]["kind"] == "SED" and rep.files[0]["fetched_at"]


# --------------------------------------------------------------------------- owner / breeder (UKC)


def test_owner_name_fills_null_takes_netkeiba_spelling_keeps_jra_van(session, archive, parity_dir):
    files, _ = load_archive_files(archive, ("UKC",), D0, D1)
    ukc = [h for h in files[0].decoded.rows if h.owner_name][:5]
    ids = [
        horse_id_from_blood_no(h.blood_reg_no, race_year=2026, birth_date=h.birth_date) for h in ukc
    ]
    session.add_all(
        [
            Horse(horse_id=ids[0], horse_name=ukc[0].horse_name, data_source="jrdb"),  # NULL → fill
            Horse(  # netkeiba spelling (no 氏, no blank) → JRDB's spelling
                horse_id=ids[1],
                horse_name=ukc[1].horse_name,
                data_source="netkeiba",
                owner_name=ukc[1].owner_name.replace("　", "").rstrip("氏"),
            ),
            Horse(
                horse_id=ids[2],
                horse_name=ukc[2].horse_name,
                data_source="jra_van",
                owner_name="別の馬主",
            ),
            Horse(horse_id=f"nk:{ids[3]}", horse_name=ukc[3].horse_name, data_source="netkeiba"),
            Horse(  # netkeiba holds a DIFFERENT owner (a sale?) → kept, reported (codex D23 #3)
                horse_id=ids[4],
                horse_name=ukc[4].horse_name,
                data_source="netkeiba",
                owner_name="新しい馬主",
            ),
        ]
    )
    session.flush()
    session.execute(
        text(
            "INSERT INTO races (race_id, race_date, race_number, venue_code) VALUES ('202605050102', '2026-09-14', 2, '05')"
        )
    )
    for n, hid in enumerate([ids[0], ids[1], ids[2], f"nk:{ids[3]}", ids[4]], start=1):
        session.execute(
            text(
                "INSERT INTO race_horses (race_id, horse_id, horse_number, entry_status) VALUES ('202605050102', :h, :n, 'started')"
            ),
            {"h": hid, "n": n},
        )
    session.commit()
    rep = _repair(session, "owner_name", archive=archive, parity_dir=parity_dir)
    assert rep.rows_changed == 3 and rep.skipped.get("kept_non_netkeiba_master") == 1
    assert rep.skipped.get("netkeiba_value_differs") == 1  # the different owner is kept
    assert session.get(Horse, ids[4]).owner_name == "新しい馬主"
    assert rep.columns_written == ("owner_name",)
    assert session.get(Horse, ids[0]).owner_name == ukc[0].owner_name
    assert session.get(Horse, ids[1]).owner_name == ukc[1].owner_name
    assert session.get(Horse, ids[2]).owner_name == "別の馬主"
    assert session.get(Horse, f"nk:{ids[3]}").owner_name == ukc[3].owner_name
    assert rep.by_year_fill_before["2026"] == {"n": 5, "filled": 3, "rate": 0.6}
    assert rep.by_year_fill_after["2026"] == {"n": 5, "filled": 5, "rate": 1.0}
    rep2 = _repair(session, "breeder_name", archive=archive, parity_dir=parity_dir)
    assert rep2.rows_changed == 5  # breeder was NULL on all five (the jra_van master too)
    before = table_snapshot(session, "horses", ("horse_id",))
    rep3 = _repair(session, "owner_name", archive=archive, parity_dir=parity_dir)
    assert rep3.rows_changed == 0
    assert before == table_snapshot(session, "horses", ("horse_id",))


# --------------------------------------------------------------------------- race columns (BAC)


def test_race_class_and_post_time_follow_precedence_and_fill_jra_van_nulls(
    session, archive, parity_dir
):
    results = _field(session)
    rids = sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})
    bac_files = load_archive_files(archive, ("BAC",), D0, D1)[0]
    bac_rows = {f.meta.data_date.year: f.decoded.rows for f in bac_files}
    bac26 = {race_id_from_key(p.key, file_year=2026): p for p in bac_rows[2026]}
    a, b, c, d = rids[:4]
    # A: netkeiba-owned static with a spelling variant → JRDB's value, static_mismatch
    session.execute(
        text("UPDATE races SET race_class = '未勝利（旧）' WHERE race_id = :r"), {"r": a}
    )
    session.execute(
        text(
            "UPDATE race_field_provenance SET source = 'netkeiba' WHERE race_id = :r AND field_group = 'race_static'"
        ),
        {"r": a},
    )
    # B: official-owned static → kept, static_mismatch
    session.execute(text("UPDATE races SET race_class = '公式の値' WHERE race_id = :r"), {"r": b})
    session.execute(
        text(
            "UPDATE race_field_provenance SET source = 'jra_official' WHERE race_id = :r AND field_group = 'race_static'"
        ),
        {"r": b},
    )
    # C: post_time NULL (no provenance) → filled; D: official-owned post_time → kept
    session.execute(text("UPDATE races SET post_time = NULL WHERE race_id = :r"), {"r": c})
    session.execute(
        text("DELETE FROM race_field_provenance WHERE race_id = :r AND field_group = 'post_time'"),
        {"r": c},
    )
    official_pt = datetime.datetime(2026, 9, 13, 15, 55, tzinfo=JST)
    session.execute(
        text("UPDATE races SET post_time = :t WHERE race_id = :r"), {"r": d, "t": official_pt}
    )
    session.execute(
        text(
            "UPDATE race_field_provenance SET source = 'jra_official' WHERE race_id = :r AND field_group = 'post_time'"
        ),
        {"r": d},
    )
    # JRA-VAN era (2024 BAC): E race_class NULL → filled; F has a value → kept
    bac24 = bac_rows[2024]
    e, f_ = (race_id_from_key(p.key, file_year=2024) for p in bac24[:2])
    session.execute(
        text(
            "INSERT INTO races (race_id, race_date, race_number, venue_code, race_class) VALUES (:r, '2024-09-13', 1, '01', NULL)"
        ),
        {"r": e},
    )
    session.execute(
        text(
            "INSERT INTO races (race_id, race_date, race_number, venue_code, race_class) VALUES (:r, '2024-09-13', 2, '01', '3歳未勝利')"
        ),
        {"r": f_},
    )
    session.commit()
    rep = _repair(session, "race_class", archive=archive, parity_dir=parity_dir)
    assert session.get(Race, a).race_class == W.RACE_CLASS_DB[bac26[a].joken_code]
    assert session.get(Race, b).race_class == "公式の値"
    assert session.get(Race, e).race_class == W.RACE_CLASS_DB[bac24[0].joken_code]
    assert session.get(Race, f_).race_class == "3歳未勝利"
    assert rep.skipped.get("static_mismatch") == 2 and rep.skipped.get("kept_jra_van") == 1
    assert rep.columns_written == ("race_class",)
    assert rep.skipped.get("kept_by_precedence") == 1
    assert rep.rows_changed == 2  # A rewritten, E filled; the other 22 already carried BAC's class
    prov = dict(
        session.execute(
            text(
                "SELECT race_id, source FROM race_field_provenance WHERE field_group = 'race_static' AND race_id IN (:a, :b, :e)"
            ),
            {"a": a, "b": b, "e": e},
        ).all()
    )
    assert prov == {a: "jrdb", b: "jra_official", e: "jrdb"}
    rep2 = _repair(session, "post_time", archive=archive, parity_dir=parity_dir)
    bac_c = datetime.datetime.combine(bac26[c].race_date, bac26[c].post_time, tzinfo=JST)
    assert session.get(Race, c).post_time == bac_c
    assert session.get(Race, d).post_time == official_pt
    assert rep2.rows_changed >= 1 and rep2.skipped.get("kept_by_precedence") == 1
    assert rep2.by_year_fill_after["2026"]["rate"] == 1.0


# --------------------------------------------------------------------------- payouts (HJC)


def test_win_payouts_from_hjc_are_written_once(session, archive, parity_dir):
    hjc = load_archive_files(archive, ("HJC",), D0, D1)[0][0].decoded.rows
    for p in hjc:
        session.execute(
            text(
                "INSERT INTO races (race_id, race_date, race_number, venue_code) VALUES (:r, :d, :n, :v)"
            ),
            {
                "r": race_id_from_key(p.key, file_year=2026),
                "d": p.race_date if hasattr(p, "race_date") else datetime.date(2026, 9, 29),
                "n": p.key.r,
                "v": p.key.venue,
            },
        )
    session.commit()
    rep = _repair(session, "win_payouts", archive=archive, parity_dir=parity_dir)
    n_win = sum(len(p.win) for p in hjc)
    assert rep.rows_changed == n_win and rep.errors == []
    row = session.execute(select(OfficialWinPayout)).scalars().first()
    assert row.source == "jrdb_result"
    assert session.scalar(select(func.count()).select_from(OfficialWinPayout)) == n_win
    before = session.execute(
        text(
            "SELECT race_id, horse_number, payout_yen, xmin::text FROM official_win_payouts ORDER BY 1, 2"
        )
    ).all()
    rep2 = _repair(session, "win_payouts", archive=archive, parity_dir=parity_dir)
    assert rep2.rows_changed == 0 and rep2.rows_unchanged == n_win
    assert (
        before
        == session.execute(
            text(
                "SELECT race_id, horse_number, payout_yen, xmin::text FROM official_win_payouts ORDER BY 1, 2"
            )
        ).all()
    )


# --------------------------------------------------------------------------- refusals


def test_jrdb_defined_column_is_refused_without_reading_the_db(session, archive, parity_dir):
    with pytest.raises(RepairColumnRefused):
        repair_column(
            session, "first_3f", D0, D1, files_dir=archive, parity_dir=parity_dir, dry_run=True
        )


# --------------------------------------------------------------------------- CLI: dry run + availability


def _args(column, archive, parity_dir, policy, tmp_path, *, dry_run=False):
    return SimpleNamespace(
        command="jrdb-repair",
        column=column,
        date_from=D0.isoformat(),
        date_to=D1.isoformat(),
        files_dir=str(archive),
        parity_dir=str(parity_dir),
        dry_run=dry_run,
        out_dir=str(tmp_path / "out"),
        database_url=None,
        policy=str(policy),
        json=True,
    )


def test_cli_dry_run_writes_nothing_and_a_real_run_records_the_availability(
    engine, archive, parity_dir, tmp_path
):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"jrdb_enabled": True}), encoding="utf-8")
    with factory() as s:
        results = _field(s)
        rids = sorted({race_id_from_key(r.key, file_year=YEAR) for r in results})
        s.execute(text("UPDATE races SET post_time = NULL WHERE race_id = :r"), {"r": rids[0]})
        s.execute(
            text(
                "DELETE FROM race_field_provenance WHERE race_id = :r AND field_group = 'post_time'"
            ),
            {"r": rids[0]},
        )
        s.commit()
    # dry run: full report, nothing written, no job row
    res = run_repair(
        _args("odds", archive, parity_dir, policy, tmp_path, dry_run=True), session_factory=factory
    )
    assert res.exit_code == 0 and res.report_path.endswith("-dryrun.json")
    report = json.loads(Path(res.report_path).read_text(encoding="utf-8"))
    assert report["dry_run"] is True and report["rows_changed"] > 0
    with factory() as s:
        assert (
            s.scalar(select(func.count()).select_from(RaceHorse).where(RaceHorse.odds.is_not(None)))
            == 0
        )
        assert s.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 0
    # real run on post_time: the job's summary.files[] is the material of the availability time
    res2 = run_repair(
        _args("post_time", archive, parity_dir, policy, tmp_path), session_factory=factory
    )
    assert res2.exit_code == 0 and res2.jobs[0]["status"] in ("succeeded", "partial")
    with factory() as s:
        av = available_at(s, rids[0], "post_time")
        assert av is not None and av.source == "jrdb" and av.kind == "BAC"
        assert (
            av.published_at is None and av.fetched_at is not None
        )  # archive file: upper bound only
        assert av.fetched_at_basis == "file_mtime" and not av.known
        job_id_first = av.ingestion_job_id
        job = s.execute(
            text("SELECT summary FROM ingestion_jobs WHERE job_type = 'jrdb_repair'")
        ).scalar_one()
        assert (
            job["files"][0]["kind"] == "BAC"
            and job["files"][0]["fetched_at"] == av.fetched_at.isoformat()
        )
    # the same file again: a second job exists, the value is unchanged, and the provenance still
    # points at the job that ADOPTED the value (data-model §3a rule a)
    res3 = run_repair(
        _args("post_time", archive, parity_dir, policy, tmp_path), session_factory=factory
    )
    assert res3.exit_code == 0
    with factory() as s:
        assert (
            s.scalar(text("SELECT count(*) FROM ingestion_jobs WHERE job_type = 'jrdb_repair'"))
            == 2
        )
        assert available_at(s, rids[0], "post_time").ingestion_job_id == job_id_first
    # a refused column exits 2 before touching the DB
    res4 = run_repair(
        _args("corner_orders", archive, parity_dir, policy, tmp_path), session_factory=factory
    )
    assert res4.exit_code == 2 and res4.error.startswith("definition_mismatch")
    with factory() as s:
        assert s.scalar(text("SELECT count(*) FROM ingestion_jobs")) == 2
    assert DAY == datetime.date(2026, 9, 13)
