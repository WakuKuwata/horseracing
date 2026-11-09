"""178 T028: the parity report's pure functions on the public sample plus a synthetic reference
(one planted defect per uncovered-cell category; meanings are transcribed, never inferred)."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

import pytest

from horseracing_scrape.jrdb import normalize as nz
from horseracing_scrape.jrdb import parity as P
from horseracing_scrape.jrdb.decode import decode_file
from horseracing_scrape.jrdb.ids import horse_id_from_blood_no, race_id_from_key
from horseracing_scrape.jrdb.models import JrdbFileMeta

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "jrdb" / "public"
DAY = datetime.date(2008, 9, 13)
CLASS_DB = {
    "A3": "未勝利",
    "A1": "新馬",
    "A2": "未出走",
    "OP": "オープン",
    "05": "500万下",
    "10": "1000万下",
    "16": "1600万下",
}


def _dec(name, kind):
    raw = (PUBLIC / f"{name}.txt").read_bytes()
    return decode_file(
        raw, JrdbFileMeta(kind, None, DAY, "daily", "x", len(raw), 0), profile="jrdb_canonical"
    )


@pytest.fixture(scope="module")
def sample():
    sed, bac, hjc = _dec("SED080913", "SED"), _dec("BAC080913", "BAC"), _dec("HJC240929", "HJC")
    return P.jrdb_year_from_decoded([sed, bac, hjc]), sed, bac


def _reference_from(sed, bac, *, person_status="mapped") -> P.Reference:
    """A DB that agrees with the sample everywhere a definition is shared; the four JRDB-defined
    columns carry DB-style values (DB first_3f = JRDB + 0.4 outside 1200 m)."""

    ref = P.Reference()
    for p in bac.rows:
        rid = race_id_from_key(p.key, file_year=2008)
        ref.races[rid] = {
            "race_id": rid,
            "race_date": p.race_date,
            "race_class": CLASS_DB.get(p.joken_code, "x"),
            "grade": None,
            "track_type": {"芝": "芝", "ダ": "ダート", "障": "障"}[
                nz.track_from_code(p.track_code)
            ],
            "distance": p.distance,
            "going": None,
            "weather": None,
            "race_name_short": p.race_name_short,
            "prize_money": p.prizes_man[0],
            "post_time": None,
            "venue_code": p.key.venue,
        }
    for r in sed.rows:
        rid = race_id_from_key(r.key, file_year=2008)
        hid = horse_id_from_blood_no(r.blood_reg_no, race_year=2008)
        ref.races[rid]["going"] = nz.going_from_code(r.going_code)
        ref.races[rid]["weather"] = nz.weather_from_code(r.weather_code)
        ref.entries[(rid, hid)] = {
            "race_id": rid,
            "horse_id": hid,
            "horse_number": r.horse_number,
            "frame": None,
            "sex": None,
            "odds": r.final_win_odds,
            "popularity": r.final_win_popularity or None,
            "weight": r.weight,
            "weight_diff": r.weight_diff if r.weight_diff is not None else 0,
            "jockey_weight": r.jockey_weight,
            "running_style": "差し",
            "jockey_id": "01019",
            "trainer_id": "01069",
            "entry_status": "started",
        }
        ref.results[(rid, hid)] = {
            "race_id": rid,
            "horse_id": hid,
            "finish_order": r.finish_order or None,
            "finish_time": r.finish_time,
            "finish_time_diff": r.finish_time_diff,
            "last_3f": r.last_3f,
            "first_3f": r.first_3f
            if r.distance == 1200
            else (r.first_3f + Decimal("0.4") if r.first_3f else None),
            "corner_orders": [str(c) for c in r.corner_orders if c] or None,
            "result_status": "finished",
        }
        ref.horses[hid] = {
            "horse_id": hid,
            "horse_name": r.horse_name,
            "sire_name": None,
            "dam_name": None,
            "damsire_name": None,
            "birth_year": None,  # no UKC in the 2008 sample set
            "owner_name": None,
            "breeder_name": None,
            "sire_line": "ノーザンダンサー系",
            "damsire_line": None,
        }
    return ref


def _mapping(sed, status="mapped"):
    pm = {}
    for r in sed.rows:
        pm[("jockey", r.jockey_code)] = status
        pm[("trainer", r.trainer_code)] = status
    return pm


def test_sample_against_an_agreeing_reference(sample):
    y, sed, bac = sample
    rep = P.build_parity(2008, y, _reference_from(sed, bac), person_mapping=_mapping(sed))
    c = rep["counts"]
    assert c["races"] == {"jrdb": 24, "db": 24, "both": 24, "jrdb_only": [], "db_only": []}
    assert c["entries"]["both"] == 324 and c["results"]["both"] == 324
    assert rep["ids"]["horse"] == {"joined": 324, "unjoined": []}
    assert rep["ids"]["jockey_codes"]["mapped"] == len({r.jockey_code for r in sed.rows})
    cols = rep["columns"]
    for name in (
        "finish_order",
        "finish_time",
        "odds",
        "popularity",
        "weight",
        "weight_diff",
        "last_3f",
        "horse_number",
    ):
        assert cols[name]["rate"] == 1.0, name
        assert all(s["rate"] == 1.0 for s in cols[name]["by_segment"].values()), name
    # race-level columns normalised across spellings (ダート/ダ, 500万下/05 …)
    for name in (
        "race_class",
        "track_type",
        "going",
        "weather",
        "distance",
        "prize_money",
        "race_name_short",
    ):
        assert cols[name]["rate"] == 1.0, name
    # first_3f: exact only at 1200 m, far below elsewhere (別定義)
    f3 = cols["first_3f"]["by_segment"]
    assert f3["1200/芝"]["rate"] == 1.0 and f3["1200/ダ"]["rate"] == 1.0
    assert all(s["rate"] < 0.1 for k, s in f3.items() if not k.startswith("1200/"))
    assert cols["first_3f"]["meaning"] == "different" and cols["corner_orders"]["threshold"] is None
    # meanings are transcribed from the contract, not inferred
    assert cols["odds"]["meaning"] == "same" and cols["race_class"]["meaning"] == "normalized"
    assert cols["post_time"]["meaning"] == "jrdb_only"
    # uncovered cells: nothing missing outside the definition-mismatch columns
    unc = rep["uncovered_reference_cells"]
    assert unc["total_excluding_definition_mismatch"] == 0
    assert unc["by_column"]["first_3f"]["definition_mismatch"] == 308
    assert unc["by_column"]["running_style"]["definition_mismatch"] == 324
    assert unc["by_column"]["sire_line"]["definition_mismatch"] == 324
    assert unc["definition_mismatch_total"] > 0
    assert set(unc["columns"]) >= {"finish_order", "odds", "horse_name", "race_class", "jockey_id"}
    assert "post_time" not in unc["columns"] and "win_payout" not in unc["columns"]
    verdict = P.passes(rep)
    assert (
        verdict["odds"] is True
        and verdict["first_3f"] is False
        and verdict["corner_orders"] is None
    )
    assert verdict["sire_name"] is None  # no data in this reference → undecided, not failed
    json.dumps(rep, default=str)  # serialisable


def test_each_uncovered_category_is_counted_once_per_planted_defect(sample):
    y, sed, bac = sample
    ref = _reference_from(sed, bac)
    keys = sorted(ref.entries)
    # row_missing: a DB entry with no JRDB row
    rid, hid = keys[0]
    ref.entries[(rid, "2006999999")] = {**ref.entries[(rid, hid)], "horse_id": "2006999999"}
    ref.results[(rid, "2006999999")] = {**ref.results[(rid, hid)], "horse_id": "2006999999"}
    # value_missing: JRDB has no odds for one horse (DB does) → make the reference odds present but
    # the JRDB value absent by pointing a second DB horse at a JRDB row without odds: simplest is a
    # race with a JRDB row whose odds are None — the public sample has none, so blank it out.
    r0 = sed.rows[0]
    blank = sed.rows[0].__class__(**{**r0.__dict__, "final_win_odds": None})
    y2 = P.JrdbYear(
        programs=y.programs,
        entries=y.entries,
        horses=y.horses,
        results=[blank, *sed.rows[1:]],
        payouts=y.payouts,
        files=y.files,
    )
    pm = _mapping(sed)
    # id_unresolved: one jockey code left unmapped
    pm[("jockey", r0.jockey_code)] = "unmapped"
    rep = P.build_parity(2008, y2, ref, person_mapping=pm)
    unc = rep["uncovered_reference_cells"]["by_column"]
    assert unc["odds"]["value_missing"] == 1
    assert unc["odds"]["row_missing"] == 1 and unc["finish_order"]["row_missing"] == 1
    n_rows_of_that_jockey = sum(1 for r in sed.rows if r.jockey_code == r0.jockey_code)
    assert unc["jockey_id"]["id_unresolved"] == n_rows_of_that_jockey
    assert unc["first_3f"]["definition_mismatch"] >= 308
    assert rep["counts"]["entries"]["db_only"] == [f"{rid}/2006999999"]
    assert rep["ids"]["jockey_codes"]["unmapped"] == 1
    assert rep["columns"]["odds"]["denominator"]["db_only"] == 1
    assert len(rep["ids"]["horse"]["unjoined"]) == 1


def test_disagreements_are_listed_up_to_ten(sample):
    y, sed, bac = sample
    ref = _reference_from(sed, bac)
    for k in sorted(ref.entries)[:15]:
        ref.entries[k]["odds"] = Decimal("999.9")
    rep = P.build_parity(2008, y, ref, person_mapping=_mapping(sed))
    ex = rep["columns"]["odds"]["examples_disagree"]
    assert len(ex) == 10 and {"race_id", "horse_id", "jrdb", "db"} <= set(ex[0])
    n = rep["columns"]["odds"]["denominator"]["both_present"]
    assert rep["columns"]["odds"]["rate"] == pytest.approx((n - 15) / n)
    assert P.passes(rep)["odds"] is False


def test_segments_and_bands():
    assert P.distance_band(1200) == "~1400" and P.distance_band(1401) == "~1800"
    assert P.distance_band(1800) == "~1800" and P.distance_band(2200) == "~2200"
    assert P.distance_band(2400) == "2201~" and P.distance_band(None) == "?"
    assert P.segment_for("odds", 1200, "芝") == "~1400/芝"
    assert (
        P.segment_for("first_3f", 1200, "ダ") == "1200/ダ"
        and P.segment_for("first_3f", 1400, "ダ") == "~1400/ダ"
    )


def test_normalisation_tables():
    assert (
        nz.class_norm("１勝クラス")
        == nz.class_norm("500万下")
        == nz.class_from_joken("05")
        == "win1"
    )
    assert nz.class_norm("２勝") == nz.class_from_joken("10") == "win2"
    assert nz.class_norm("３歳未勝利") == nz.class_from_joken("A3") == "maiden"
    assert nz.class_norm("オープン") == nz.class_norm("G3") == nz.class_from_joken("OP") == "open"
    assert (
        nz.track_norm("ダート") == nz.track_from_code(2) == "ダ" and nz.track_norm("障害") == "障"
    )
    assert (
        nz.going_from_code(11) == nz.going_from_code(12) == "良"
        and nz.going_from_code(42) == nz.going_norm("不良") == "不"
    )
    assert nz.grade_norm("GIII") == nz.grade_from_code(3) == "G3" and nz.grade_from_code(6) == "L"
    assert nz.sex_norm("セン") == nz.sex_from_code(3) == "セ"
    assert nz.norm_name("安原　浩司氏", strip_shi=True) == "安原浩司" == nz.norm_name("安原 浩司")
    assert (
        nz.seconds(datetime.timedelta(seconds=70.3)) == Decimal("70.3") and nz.seconds(None) is None
    )


def test_r4_representation_rules():
    """R4 (2026-11-09): the comparator's representation rules fixed after the first 18-year run —
    thresholds unchanged. DB grade letters, 法人略号, small kana, JRDB field truncation."""

    from horseracing_scrape.jrdb import normalize as nz

    assert (
        nz.grade_norm("E") == "特別" and nz.grade_norm("B") == "G2" and nz.grade_norm("H") == "G3"
    )
    assert nz.grade_norm("GII") == "G2" and nz.grade_norm("L") == "L"
    assert nz.norm_name("(有)サンデーレーシング", strip_corp=True) == "サンデーレーシング"
    assert nz.norm_name("レッドマジック(株)", strip_corp=True) == "レッドマジック"
    assert nz.norm_name("ワッスルタッチ", fold_kana=True) == nz.norm_name(
        "ワツスルタツチ", fold_kana=True
    )
    # a JRDB value that fills its field (全角 20 文字, measured on the RAW value) is compared on
    # the prefix of the normalized value; a shorter one is not
    raw = "Ｗｈｉｓｐｅｒ　Ｈｉｌｌ　Ｆａｒｍ　ＬＬ"  # 20 full-width chars = the UKC cut
    jv = nz.norm_name(raw, fold_kana=True)  # "WhisperHillFarmLL"
    assert nz.prefix_if_truncated(jv, "WhisperHillFarmLLC", 40, raw=raw) == (jv, jv)
    short = "Ｓｍａｌｌ　Ｆａｒｍ"
    sv = nz.norm_name(short)
    assert nz.prefix_if_truncated(sv, "SmallFarmLLC", 40, raw=short) == (sv, "SmallFarmLLC")
    assert nz.prefix_if_truncated("小峰城特", "小峰城特500", 8, raw="小峰城特") == (
        "小峰城特",
        "小峰城特",
    )
    assert nz.prefix_if_truncated("渡島", "渡島特別", 8, raw="渡島　　") == ("渡島", "渡島特別")
