"""178 T012: race key → race_id and blood number → horse_id are deterministic and fail-closed."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from horseracing_scrape.jrdb import ids
from horseracing_scrape.jrdb.decode import decode_file
from horseracing_scrape.jrdb.errors import JrdbDecodeError
from horseracing_scrape.jrdb.models import JrdbFileMeta

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "jrdb" / "public"


@pytest.mark.parametrize(
    ("key8", "file_year", "race_id"),
    [
        ("01082101", 2008, "200801020101"),  # 2008-09-13 札幌 2 回 1 日 1R (SED/BAC sample)
        ("02152201", 2015, "201502020201"),  # 2015-07-12 函館
        ("06214201", 2021, "202106040201"),  # 2021-09-12 中山
        ("06244901", 2024, "202406040901"),  # 2024-09-29 中山 4 回 9 日
        ("05261a05", 2026, "202605011005"),  # 日 'a' = 10 (16 進)
        ("09261c12", 2026, "202609011212"),  # 日 'c' = 12
        ("01995a10", 1999, "199901051010"),  # yy 99 → 1999
    ],
)
def test_race_id_from_key(key8, file_year, race_id):
    assert ids.race_id_from_key(key8, file_year=file_year) == race_id


@pytest.mark.parametrize("venue", ["21", "35", "42", "61", "66", "79"])
def test_non_jra_venues_are_not_mapped(venue):
    assert ids.race_id_from_key(f"{venue}082101", file_year=2008) is None


def test_file_year_mismatch_is_an_error_not_a_wrong_id():
    with pytest.raises(JrdbDecodeError):
        ids.race_id_from_key("01082101", file_year=2015)


@pytest.mark.parametrize("bad", ["0108210", "01082g01", "ab082101"])
def test_malformed_keys_are_errors(bad):
    with pytest.raises(JrdbDecodeError):
        ids.race_id_from_key(bad)


@pytest.mark.parametrize(
    ("name", "kind", "day"),
    [
        ("BAC080913", "BAC", datetime.date(2008, 9, 13)),
        ("SED080913", "SED", datetime.date(2008, 9, 13)),
        ("KYI150712", "KYI", datetime.date(2015, 7, 12)),
        ("TYB210912", "TYB", datetime.date(2021, 9, 12)),
        ("HJC240929", "HJC", datetime.date(2024, 9, 29)),
    ],
)
def test_every_sample_race_maps_to_a_well_formed_jra_race_id(name, kind, day):
    raw = (PUBLIC / f"{name}.txt").read_bytes()
    df = decode_file(
        raw, JrdbFileMeta(kind, None, day, "daily", "x", len(raw), 0), profile="current"
    )
    rids = {ids.race_id_from_key(r.key, file_year=day.year) for r in df.rows}
    assert None not in rids
    for rid in rids:
        assert len(rid) == 12 and rid.isdigit()
        assert rid[:4] == str(day.year)
        assert rid[4:6] in ids.JRA_VENUE_CODES
        assert 1 <= int(rid[10:12]) <= 12
    # 24 races per venue-day in the per-race files
    if kind in ("BAC", "HJC"):
        assert len(rids) == 24


def test_horse_id_century_rule_and_birth_date_check():
    assert ids.horse_id_from_blood_no("99101712", race_year=2008) == "1999101712"
    assert ids.horse_id_from_blood_no("00101712", race_year=2008) == "2000101712"
    assert ids.horse_id_from_blood_no("06102843", race_year=2008) == "2006102843"
    with pytest.raises(JrdbDecodeError):  # born in the race year = age 0 → implausible
        ids.horse_id_from_blood_no("08100001", race_year=2008)
    # 1999 race, yy 98 → 1998 (not 2098); 2008 race, yy 99 → 1999
    assert ids.horse_id_from_blood_no("98100001", race_year=1999) == "1998100001"
    assert ids.horse_id_from_blood_no("05100001", race_year=2008) == "2005100001"
    assert (
        ids.horse_id_from_blood_no(
            "99101712", race_year=2008, birth_date=datetime.date(1999, 5, 31)
        )
        == "1999101712"
    )
    with pytest.raises(JrdbDecodeError):
        ids.horse_id_from_blood_no(
            "99101712", race_year=2008, birth_date=datetime.date(2000, 5, 31)
        )
    with pytest.raises(JrdbDecodeError):
        ids.horse_id_from_blood_no("9910171", race_year=2008)


def test_sample_horses_all_join_to_ten_digit_ids():
    raw = (PUBLIC / "SED080913.txt").read_bytes()
    df = decode_file(
        raw,
        JrdbFileMeta("SED", None, datetime.date(2008, 9, 13), "daily", "x", len(raw), 0),
        profile="current",
    )
    hids = [ids.horse_id_from_blood_no(r.blood_reg_no, race_year=2008) for r in df.rows]
    assert len(hids) == 324 and all(len(h) == 10 and 1996 <= int(h[:4]) <= 2006 for h in hids)


def test_venue_code_identity_for_jra_only():
    assert ids.venue_code_jrdb_to_db("05") == "05" and ids.venue_code_jrdb_to_db("5") == "05"
    assert ids.venue_code_jrdb_to_db("35") is None and ids.venue_code_jrdb_to_db(None) is None


def test_resolve_year_uses_the_file_year_as_reference():
    assert ids.resolve_year(8, reference_year=2008) == 2008
    assert ids.resolve_year(99, reference_year=1999) == 1999
    assert ids.resolve_year(99, reference_year=2000) == 1999  # year-end neighbour
    assert ids.resolve_year(0, reference_year=1999) == 2000
    assert (
        ids.resolve_year(95, reference_year=2095) == 2095
    )  # no fixed 90 boundary when a reference exists
    with pytest.raises(JrdbDecodeError):
        ids.resolve_year(8, reference_year=2015)
    # contract fallback without a reference
    assert ids.resolve_year(99, reference_year=None) == 1999
    assert ids.resolve_year(26, reference_year=None) == 2026


def test_race_key_grammar_is_checked_at_both_entry_points():
    with pytest.raises(JrdbDecodeError):
        ids.race_id_from_key("01082001", file_year=2008)  # day 0 is not a day
    with pytest.raises(ValueError):
        ids.JrdbRaceKey(
            venue="01", yy=8, kai=2, day_hex="10", r=1
        )  # two chars are not one hex digit
    with pytest.raises(ValueError):
        ids.JrdbRaceKey(venue="1", yy=8, kai=2, day_hex="1", r=1)
    with pytest.raises(JrdbDecodeError):
        ids.race_id_from_key(
            ids.JrdbRaceKey(venue="01", yy=8, kai=2, day_hex="1", r=13), file_year=2008
        )
