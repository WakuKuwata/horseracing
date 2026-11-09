"""178 T038: ``jrdb-parity`` against a DB seeded from the 2008-09-13 public sample — product tables
untouched (row counts and xmin), agreement where definitions match, the 別定義 columns reported,
the four uncovered-cell categories detectable, and the contract's key set."""

from __future__ import annotations

import datetime
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from horseracing_scrape.jrdb import normalize as nz
from horseracing_scrape.jrdb.cli import main
from horseracing_scrape.jrdb.decode import decode_file
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from horseracing_scrape.jrdb.models import JrdbFileMeta

pytestmark = pytest.mark.integration

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "jrdb" / "public"
DAY = datetime.date(2008, 9, 13)
TABLES = (
    "races",
    "horses",
    "jockeys",
    "trainers",
    "race_horses",
    "race_results",
    "official_win_payouts",
    "exotic_odds",
    "race_laps",
)
CLASS_DB = {
    "A3": "未勝利",
    "A1": "新馬",
    "OP": "オープン",
    "05": "500万下",
    "10": "1000万下",
    "16": "1600万下",
}


def _dec(name, kind):
    raw = (PUBLIC / f"{name}.txt").read_bytes()
    meta = JrdbFileMeta(kind, None, DAY, "daily", "x", len(raw), 0)
    return decode_file(raw, meta, profile="jrdb_canonical")


def _seed(session, *, plant: bool):
    sed, bac = _dec("SED080913", "SED"), _dec("BAC080913", "BAC")
    session.execute(text("INSERT INTO jockeys (jockey_id, jockey_name) VALUES ('01019', '騎手')"))
    session.execute(
        text("INSERT INTO trainers (trainer_id, trainer_name) VALUES ('01069', '調教師')")
    )
    for p in bac.rows:
        rid = race_id_from_key(p.key, file_year=2008)
        session.execute(
            text(
                "INSERT INTO races (race_id, race_date, race_number, venue_code, distance, "
                "track_type, race_class, race_name_short, prize_money) VALUES "
                "(:rid, :d, :no, :v, :dist, :tt, :cls, :short, :prize)"
            ),
            {
                "rid": rid,
                "d": p.race_date,
                "no": p.key.r,
                "v": p.key.venue,
                "dist": p.distance,
                "tt": nz.track_from_code(p.track_code),
                "cls": CLASS_DB.get(p.joken_code, "x"),
                "short": p.race_name_short,
                "prize": p.prizes_man[0],
            },
        )
    seen_h: set[str] = set()
    planted: dict[str, tuple[str, str]] = {}
    for r in sed.rows:
        rid = race_id_from_key(r.key, file_year=2008)
        hid = horse_id_from_blood_no(r.blood_reg_no, race_year=2008)
        session.execute(
            text("UPDATE races SET going = :g, weather = :w WHERE race_id = :rid"),
            {
                "g": nz.going_from_code(r.going_code),
                "w": nz.weather_from_code(r.weather_code),
                "rid": rid,
            },
        )
        if hid not in seen_h:
            seen_h.add(hid)
            session.execute(
                text(
                    "INSERT INTO horses (horse_id, horse_name, sire_line) "
                    "VALUES (:h, :n, 'ノーザンダンサー系')"
                ),
                {"h": hid, "n": r.horse_name},
            )
        odds = r.final_win_odds
        if plant and odds is None and "value_missing" not in planted:
            odds = Decimal("9.9")  # DB has odds where JRDB has none → value_missing
            planted["value_missing"] = (rid, hid)
        entry_status = {1: "cancelled", 2: "excluded"}.get(r.abnormal_code or 0, "started")
        session.execute(
            text(
                "INSERT INTO race_horses (race_id, horse_id, horse_number, odds, popularity, weight, "
                "weight_diff, jockey_weight, running_style, jockey_id, trainer_id, entry_status) "
                "VALUES (:rid, :hid, :num, :odds, :pop, :w, :wd, :jw, '差し', '01019', '01069', :st)"
            ),
            {
                "rid": rid,
                "hid": hid,
                "num": r.horse_number,
                "odds": odds,
                "pop": r.final_win_popularity or None,
                "w": r.weight,
                "wd": r.weight_diff if r.weight_diff is not None else 0,
                "jw": r.jockey_weight,
                "st": entry_status,
            },
        )
        if entry_status != "started":
            continue  # a non-starter has no race_results row (INV-1)
        result_status = {3: "stopped", 4: "disqualified"}.get(r.abnormal_code or 0, "finished")
        if result_status == "finished" and not r.finish_order:
            continue
        if r.distance == 1200 or r.first_3f is None:
            first_3f = r.first_3f
        else:
            first_3f = r.first_3f + Decimal("0.4")  # the DB's own definition differs off 1200 m
        session.execute(
            text(
                "INSERT INTO race_results (race_id, horse_id, finish_order, finish_time, "
                "finish_time_diff, last_3f, first_3f, corner_orders, result_status) VALUES "
                "(:rid, :hid, :fo, :ft, :ftd, :l3, :f3, :co, :rs)"
            ),
            {
                "rid": rid,
                "hid": hid,
                "fo": r.finish_order or None,
                "ft": r.finish_time,
                "ftd": (
                    datetime.timedelta(seconds=float(r.finish_time_diff))
                    if r.finish_time_diff is not None
                    else None
                ),
                "l3": r.last_3f,
                "f3": first_3f,
                "co": [str(c) for c in r.corner_orders if c] or None,
                "rs": result_status,
            },
        )
        if r.finish_order == 1 and r.win_payout_yen:
            session.execute(
                text(
                    "INSERT INTO official_win_payouts (race_id, horse_number, payout_yen, source, "
                    "observed_at) VALUES (:rid, :num, :yen, 'netkeiba_result', now())"
                ),
                {"rid": rid, "num": r.horse_number, "yen": r.win_payout_yen},
            )
    if plant:
        # row_missing: a DB entry that JRDB does not have
        rid = race_id_from_key(sed.rows[0].key, file_year=2008)
        session.execute(text("INSERT INTO horses (horse_id, horse_name) VALUES ('2006999999', '幽霊')"))
        session.execute(
            text(
                "INSERT INTO race_horses (race_id, horse_id, horse_number, odds, entry_status) "
                "VALUES (:rid, '2006999999', 99, 1.5, 'started')"
            ),
            {"rid": rid},
        )
        planted["row_missing"] = (rid, "2006999999")
    session.commit()
    return planted


def _snapshot(session) -> dict[str, tuple[int, int | None]]:
    out = {}
    for t in TABLES:
        n, xm = session.execute(text(f"SELECT count(*), max(xmin::text::bigint) FROM {t}")).one()
        out[t] = (int(n), int(xm) if xm is not None else None)
    return out


def _files_dir(tmp_path: Path) -> Path:
    d = tmp_path / "files"
    for name in ("BAC080913.txt", "SED080913.txt", "SRB080913.txt", "HJC240929.txt"):
        (d / name[:3] / "20080913").mkdir(parents=True, exist_ok=True)
        shutil.copy(PUBLIC / name, d / name[:3] / "20080913" / name)
    return d


def _run(files_dir: Path, out: Path, database_url: str) -> int:
    return main(
        [
            "jrdb-parity",
            "--year",
            "2008",
            "--files-dir",
            str(files_dir),
            "--out",
            str(out),
            "--db-url",
            database_url,
            "--json",
        ]
    )


def test_parity_is_read_only_and_reports_the_contract(session, database_url, tmp_path):
    planted = _seed(session, plant=True)
    before = _snapshot(session)
    out = tmp_path / "2008.json"
    files_dir = _files_dir(tmp_path)
    assert _run(files_dir, out, database_url) == 0
    session.expire_all()
    assert _snapshot(session) == before, "parity must not write to any product table"
    rep = json.loads(out.read_text(encoding="utf-8"))
    # (5) contract key set
    assert set(rep) >= {"year", "files", "counts", "ids", "columns", "uncovered_reference_cells"}
    assert set(rep["counts"]) == {"races", "entries", "results", "payout_rows"}
    col = rep["columns"]["finish_order"]
    assert set(col) >= {"meaning", "denominator", "by_segment", "examples_disagree"}
    # (2) finish_order agrees in every segment
    assert col["by_segment"] and all(s["rate"] == 1.0 for s in col["by_segment"].values())
    # (3) first_3f exact at 1200 m only
    f3 = rep["columns"]["first_3f"]["by_segment"]
    assert f3["1200/芝"]["rate"] == 1.0 and f3["1200/ダ"]["rate"] == 1.0
    assert all(s["rate"] < 0.1 for k, s in f3.items() if not k.startswith("1200/"))
    # (4) the four categories are each detectable
    unc = rep["uncovered_reference_cells"]["by_column"]
    assert unc["odds"]["value_missing"] == 1
    assert unc["odds"]["row_missing"] == 1 and unc["horse_name"]["row_missing"] == 1
    assert unc["jockey_id"]["id_unresolved"] == 324  # no persons report → all unresolved
    assert unc["first_3f"]["definition_mismatch"] > 0
    assert unc["sire_line"]["definition_mismatch"] == 324
    assert rep["counts"]["entries"]["db_only"] == [f"{planted['row_missing'][0]}/2006999999"]
    assert rep["counts"]["races"]["both"] == 24 and rep["ids"]["horse"]["joined"] == 324
    assert rep["columns"]["win_payout"]["rate"] is None  # HJC sample is 2024: no 2008 payout join
    assert rep["rejected_rows"] == 0
    # an existing output path is refused
    assert _run(files_dir, out, database_url) == 2


def test_parity_with_a_clean_reference_has_no_uncovered_cells_outside_definition_mismatch(
    session, database_url, tmp_path
):
    _seed(session, plant=False)
    out = tmp_path / "clean.json"
    assert _run(_files_dir(tmp_path), out, database_url) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    unc = rep["uncovered_reference_cells"]
    by = unc["by_column"]
    # jockey/trainer ids stay unresolved without a persons report; everything else is covered
    others = sum(
        v
        for name, d in by.items()
        if name not in ("jockey_id", "trainer_id")
        for k, v in d.items()
        if k != "definition_mismatch"
    )
    assert others == 0
    assert unc["definition_mismatch_total"] > 0
    assert rep["columns"]["odds"]["rate"] == 1.0 and rep["columns"]["race_class"]["rate"] == 1.0
    assert rep["columns"]["finish_time"]["rate"] == 1.0 and rep["columns"]["last_3f"]["rate"] == 1.0
