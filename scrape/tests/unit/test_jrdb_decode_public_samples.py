"""178 T011: the layouts decode the public samples to the values measured in research D1."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from horseracing_scrape.jrdb.decode import decode_file
from horseracing_scrape.jrdb.errors import JrdbLayoutMismatch
from horseracing_scrape.jrdb.models import JrdbFileMeta

PUBLIC = Path(__file__).resolve().parents[1] / "fixtures" / "jrdb" / "public"


def _dec(name: str, kind: str, day: datetime.date, profile: str = "jrdb_canonical"):
    raw = (PUBLIC / f"{name}.txt").read_bytes()
    meta = JrdbFileMeta(kind, None, day, "daily", "x", len(raw), 0)
    return raw, decode_file(raw, meta, profile=profile)


def test_sed_2008_09_13_first_row():
    _raw, df = _dec("SED080913", "SED", datetime.date(2008, 9, 13))
    assert df.n_rows == 324 and not df.rejected_rows and df.record_length == 376
    r = df.rows[0]
    assert r.key.key8 == "01082101" and r.horse_number == 1 and r.blood_reg_no == "06102843"
    assert r.horse_name == "ヒカルジョディー"
    assert r.finish_order == 1 and r.abnormal_code == 0
    assert r.finish_time == datetime.timedelta(seconds=70.3)
    assert r.final_win_odds == Decimal("8.8") and r.final_win_popularity == 3
    assert r.jockey_weight == Decimal("54.0") and r.weight == 466 and r.weight_diff == -4
    assert r.last_3f == Decimal("35.6") and r.finish_time_diff == Decimal("0.1")
    assert r.win_payout_yen == 880 == int(r.final_win_odds * 100)  # 払戻 = 確定オッズ×100 (D1)
    assert r.jockey_code == "10446" and r.trainer_code == "10379"  # JRDB 独自コード (D3)
    assert r.post_time is None  # 第４版a (2022-08) より前のサンプル
    # canonical-only columns are decoded under jrdb_canonical
    assert r.first_3f == Decimal("34.7") and r.running_style_code == "3"
    assert r.corner_orders == (None, 3, 3, 4)


def test_sed_profile_current_drops_the_four_canonical_only_columns():
    _raw, df = _dec("SED080913", "SED", datetime.date(2008, 9, 13), profile="current")
    assert df.n_rows == 324
    assert all(r.first_3f is None for r in df.rows)
    assert all(r.corner_orders is None for r in df.rows)
    assert all(r.running_style_code is None for r in df.rows)
    # everything adopted is still there
    assert df.rows[0].final_win_odds == Decimal("8.8")


def test_sed_first_3f_present_only_under_canonical_and_mostly_filled():
    _raw, df = _dec("SED080913", "SED", datetime.date(2008, 9, 13))
    assert sum(1 for r in df.rows if r.distance == 1200) == 102
    assert sum(1 for r in df.rows if r.first_3f is not None) == 308


def test_kyi_2015_07_12():
    _raw, df = _dec("KYI150712", "KYI", datetime.date(2015, 7, 12))
    assert df.n_rows == 498 and not df.rejected_rows and df.record_length == 1024
    r = df.rows[0]
    assert r.key.key8 == "02152201" and r.horse_number == 1 and r.frame == 1
    assert r.blood_reg_no == "13103588" and r.horse_name == "ウェルノーテッド"
    assert r.sex_code == 2 and r.carried_weight == Decimal("54.0")
    assert r.jockey_name == "四位洋文" and r.jockey_code == "10390"
    assert (
        r.trainer_name == "昆貢" and r.trainer_affiliation == "栗東" and r.trainer_code == "10356"
    )
    assert r.owner_name == "安原　浩司氏"  # 全角空白+「氏」はそのまま(正規化は parity 側)
    assert r.weight is None and r.weight_diff is None  # 前日版の枠確定馬体重は空
    apprentice = [x for x in df.rows if x.carried_weight == Decimal("51.0")]
    assert apprentice and all(x.apprentice_code == 3 for x in apprentice)  # ▲ 3 kg 減
    assert {x.key.venue for x in df.rows} == {"02", "03", "07"}


def test_bac_2008_09_13():
    _raw, df = _dec("BAC080913", "BAC", datetime.date(2008, 9, 13))
    assert df.n_rows == 24 and df.record_length == 184
    r = df.rows[0]
    assert r.key.key8 == "01082101" and r.race_date == datetime.date(2008, 9, 13)
    assert r.post_time == datetime.time(10, 0) and r.distance == 1200 and r.track_code == 1
    assert r.joken_code == "A3" and r.shubetsu_code == 11 and r.head_count == 8
    assert r.prizes_man == (500, 200, 130, 75, 50)
    assert r.race_name_9 == "２歳未勝利"
    assert [x.post_time.strftime("%H:%M") for x in df.rows[:3]] == ["10:00", "10:30", "11:00"]


def test_ukc_2002_09_08():
    _raw, df = _dec("UKC020908", "UKC", datetime.date(2002, 9, 8))
    assert df.n_rows == 478 and df.record_length == 292 and df.line_terminator == "LFLF"
    r = df.rows[0]
    assert r.blood_reg_no == "99101712" and r.horse_name == "アドマイヤエディ"
    assert r.birth_date == datetime.date(1999, 5, 31) and r.sex_code == 1
    assert (r.sire_name, r.dam_name, r.damsire_name) == (
        "コマンダーインチーフ",
        "アドマイヤエール",
        "ジェネラス",
    )
    assert r.owner_name == "近藤　利一氏" and r.breeder_name == "辻　牧場"
    assert r.sire_line_code == "1104" and r.damsire_line_code == "1102"
    _raw, cur = _dec("UKC020908", "UKC", datetime.date(2002, 9, 8), profile="current")
    assert all(x.sire_line_code is None and x.damsire_line_code is None for x in cur.rows)


def test_ksa_2002_09_07_okabe():
    _raw, df = _dec("KSA020907", "KSA", datetime.date(2002, 9, 7))
    assert df.n_rows == 172 and df.record_length == 272
    r = df.rows[0]
    assert r.kind == "jockey" and r.code == "10076" and r.name == "岡部幸雄"
    assert r.birth_date == datetime.date(1948, 10, 31) and r.first_license_year == 1967
    assert r.affiliation_code == 1 and r.retired is False
    assert r.kana == "オカベ　ユキオ"


def test_hjc_2024_09_29_win_payouts_for_all_24_races():
    _raw, df = _dec("HJC240929", "HJC", datetime.date(2024, 9, 29))
    assert df.n_rows == 24 and df.record_length == 444 and df.line_terminator == "LF"
    assert all(r.win for r in df.rows)
    r = df.rows[0]
    assert r.key.key8 == "06244901"
    assert r.win == ((4, 750),) and r.place == ((4, 230), (12, 150), (15, 440))
    assert r.quinella == (((4, 12), 1420),) and r.exacta == (((4, 12), 2920),)
    assert r.trio == (((4, 12, 15), 8550),) and r.trifecta == (((4, 12, 15), 39040),)
    assert len(r.wide) == 3
    assert max(y for x in df.rows for _c, y in x.trifecta) == 681030  # 9 桁の払戻欄


def test_srb_2008_09_13_laps_sum_to_the_winning_time():
    _raw, srb = _dec("SRB080913", "SRB", datetime.date(2008, 9, 13))
    _raw, sed = _dec("SED080913", "SED", datetime.date(2008, 9, 13))
    assert srb.n_rows == 24 and srb.record_length == 852
    r = srb.rows[0]
    assert r.lap_times == tuple(
        Decimal(x) for x in ("12.1", "11.0", "11.4", "11.8", "11.4", "12.6")
    )
    assert (
        sum(r.lap_times)
        == Decimal("70.3")
        == Decimal(sed.rows[0].finish_time.total_seconds()).quantize(Decimal("0.1"))
    )
    assert r.track_bias["bias_corner_4"] == "24433" and r.track_bias["bias_corner_1"] is None


def test_tyb_2021_09_12_signed_weight_diff():
    raw, df = _dec("TYB210912", "TYB", datetime.date(2021, 9, 12))
    assert df.n_rows == 315 and df.record_length == 128 and df.line_terminator == "LF"
    r = df.rows[0]
    assert r.key.key8 == "06214201" and r.horse_number == 1
    assert r.jockey_code == "10570" and r.jockey_name == "石川裕紀人"
    assert r.carried_weight == Decimal("54.0") and r.weight == 484 and r.weight_diff == -6
    assert r.going_code == 10 and r.weather_code == 2 and r.cancelled is False
    # ``XZ9`` with an inner blank (``- 4``) → -4
    minus4 = [x for x in df.rows if x.weight_diff == -4]
    assert len(minus4) == sum(1 for line in raw.split(b"\n") if line[91:94] == b"- 4") == 19
    assert all(x.post_time is None for x in df.rows)  # 第４b版 (2022-09) より前


def test_a_row_with_a_missing_byte_is_rejected_and_never_built():
    raw = (PUBLIC / "SED080913.txt").read_bytes()
    lines = raw.split(b"\r\n")
    assert lines[-1] == b""
    lines[1] = lines[1][:-1]  # 1 byte short
    broken = b"\r\n".join(lines)
    meta = JrdbFileMeta("SED", None, datetime.date(2008, 9, 13), "daily", "x", len(broken), 0)
    df = decode_file(broken, meta, profile="current")
    assert df.n_rows == 323
    assert [(r.line_no, r.actual_length) for r in df.rejected_rows] == [(2, 373)]
    assert not hasattr(df.rejected_rows[0], "data")  # no content is kept from a rejected row


def test_a_file_in_another_layout_is_a_layout_mismatch():
    raw = (PUBLIC / "SED080913.txt").read_bytes()
    meta = JrdbFileMeta("KYI", None, datetime.date(2008, 9, 13), "daily", "x", len(raw), 0)
    with pytest.raises(JrdbLayoutMismatch):
        decode_file(raw, meta, profile="current")


def test_unknown_profile_is_refused():
    raw = (PUBLIC / "BAC080913.txt").read_bytes()
    meta = JrdbFileMeta("BAC", None, datetime.date(2008, 9, 13), "daily", "x", len(raw), 0)
    with pytest.raises(ValueError):
        decode_file(raw, meta, profile="production")


# --- codex D20: no silently wrong values --------------------------------------------------------

from horseracing_scrape.jrdb.decode import decode_field, split_records  # noqa: E402
from horseracing_scrape.jrdb.errors import JrdbDecodeError  # noqa: E402
from horseracing_scrape.jrdb.layout import Field  # noqa: E402


def _f(**kw) -> Field:
    base = dict(name="x", start=1, length=4, type="9", fmt="9999")
    base.update(kw)
    return Field(**base)


@pytest.mark.parametrize("raw", [b"1.  ", b"1e2 ", b" NaN", b"12 3", b"+12 "])
def test_integer_pictures_reject_non_integer_text(raw):
    with pytest.raises(JrdbDecodeError):
        decode_field(raw, _f(), 1)


def test_integer_picture_accepts_blank_minus_and_digits():
    assert decode_field(b"    ", _f(), 1) is None
    assert decode_field(b" -12", _f(), 1) == -12
    assert decode_field(b"0540", _f(fmt="999", scale=10, length=4), 1) == Decimal("54")


@pytest.mark.parametrize("raw", [b" 12  ", b"12.  ", b"1e2.0", b"  inf"])
def test_decimal_pictures_require_the_literal_point(raw):
    with pytest.raises(JrdbDecodeError):
        decode_field(raw, _f(type="Z", fmt="ZZ9.9", length=5), 1)


def test_signed_xz9_rules():
    f = _f(type="X", fmt="XZ9", length=3, codec="signed")
    assert decode_field(b"- 4", f, 1) == -4
    assert decode_field(b"+12", f, 1) == 12
    assert decode_field(b"  0", f, 1) == 0
    assert decode_field(b"   ", f, 1) is None
    for raw in (b"-  ", b"+  ", b" -4", b"--4"):
        with pytest.raises(JrdbDecodeError):
            decode_field(raw, f, 1)


def test_time_and_date_pictures_are_range_checked():
    t = _f(codec="time_m_sss")
    assert decode_field(b"1103", t, 1) == datetime.timedelta(seconds=70.3)
    assert decode_field(b"0000", t, 1) is None
    with pytest.raises(JrdbDecodeError):
        decode_field(b"1600", t, 1)  # 60.0 s is not a seconds part
    d = _f(length=8, fmt="9", codec="yyyymmdd")
    assert decode_field(b"20080913", d, 1) == datetime.date(2008, 9, 13)
    with pytest.raises(JrdbDecodeError):
        decode_field(b"2008091 ", d, 1)
    with pytest.raises(JrdbDecodeError):
        decode_field(b"20081332", d, 1)
    y = _f(codec="yyyy")
    with pytest.raises(JrdbDecodeError):
        decode_field(b"196 ", y, 1)
    flag = _f(length=1, fmt="9", codec="flag")
    assert decode_field(b"1", flag, 1) is True and decode_field(b"0", flag, 1) is False
    assert decode_field(b" ", flag, 1) is False
    with pytest.raises(JrdbDecodeError):
        decode_field(b"2", flag, 1)


def test_framing_rejects_bare_cr_unterminated_tail_and_reports_mixed_terminators():
    data = b"a" * 10
    raw = data + b"\r\n" + data + b"\n" + data + b"\n\n" + b"b" * 9 + b"\r" + b"c" + b"\n" + data
    records, rejected, framing = split_records(raw, 10)
    assert [r for _l, r, _d in records] == [1, 2, 3]
    assert framing.terminator == "mixed" and framing.counts == {"CRLF": 1, "LF": 1, "LFLF": 1}
    assert [(r.record_no, r.reason) for r in rejected] == [(4, "cr_in_data"), (5, "unterminated")]
    # physical line numbers survive the blank line of the LFLF record
    assert [r.line_no for r in rejected] == [5, 6]


def test_framing_counts_extra_blank_lines_without_making_records():
    raw = b"a" * 4 + b"\n\n\n\n" + b"b" * 4 + b"\n"
    records, rejected, framing = split_records(raw, 4)
    assert len(records) == 2 and not rejected
    assert framing.terminator == "mixed" and framing.extra_blank_lines == 2


def test_decode_error_messages_never_contain_the_bytes():
    f = _f(type="X", fmt="X", length=8, codec="code")
    raw = (PUBLIC / "SED080913.txt").read_bytes()
    broken = bytearray(raw)
    broken[10:18] = b"9X9X9X9X"  # blood_reg_no of the first row
    meta = JrdbFileMeta("SED", None, datetime.date(2008, 9, 13), "daily", "x", len(raw), 0)
    rows = decode_file(bytes(broken), meta, profile="current").rows
    assert rows[0].blood_reg_no == "9X9X9X9X"  # a code field keeps text; the id layer rejects it
    with pytest.raises(JrdbDecodeError) as exc:
        decode_field(b"12ab", _f(), 3)
    assert (
        "12ab" not in str(exc.value) and "line 3" in str(exc.value) and exc.value.__cause__ is None
    )
    del f


def test_ukc_canonical_columns_are_dropped_even_through_the_builder():
    from horseracing_scrape.jrdb import decode as dec

    d = {"blood_reg_no": "99101712", "sire_line_code": "1104", "damsire_line_code": "1102"}
    row = dec._build_row("UKC", d, line_no=1, include_canonical=False)
    assert row.sire_line_code is None and row.damsire_line_code is None
    row = dec._build_row("UKC", d, line_no=1, include_canonical=True)
    assert row.sire_line_code == "1104"


def test_body_weight_zero_means_no_measurement():
    """R5 (2026-11-09): SED/TYB/KYI ``000`` 馬体重 (取消・除外・未計量) decodes to None, never 0 kg,
    and the 増減 is dropped with it; a real weight keeps its diff."""

    import datetime

    from horseracing_scrape.jrdb.decode import decode_file
    from horseracing_scrape.jrdb.models import JrdbFileMeta
    from tests._jrdb_synth import _template, patch_record

    def one(kind, values, day):
        rec = patch_record(kind, _template(kind), values) + b"\r\n"
        meta = JrdbFileMeta(kind, None, day, "daily", "x", len(rec), 0)
        return decode_file(rec, meta, profile="jrdb_canonical").rows[0]

    sed_zero = one("SED", {"weight": 0, "weight_diff": None}, datetime.date(2008, 9, 13))
    assert sed_zero.weight is None and sed_zero.weight_diff is None
    sed_real = one("SED", {"weight": 480, "weight_diff": -4}, datetime.date(2008, 9, 13))
    assert sed_real.weight == 480 and sed_real.weight_diff == -4
    tyb_zero = one("TYB", {"weight": 0, "weight_diff": 0}, datetime.date(2021, 9, 12))
    assert tyb_zero.weight is None and tyb_zero.weight_diff is None
    kyi_zero = one("KYI", {"weight": 0, "weight_diff": None}, datetime.date(2015, 7, 12))
    assert kyi_zero.weight is None
