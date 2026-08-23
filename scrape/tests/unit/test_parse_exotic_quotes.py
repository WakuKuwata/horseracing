"""PRE-RACE exotic price grid parser — the losing combinations are the whole point.

`exotic_odds` only ever holds the dividend of the combination that came in, so it cannot drive
selection. This grid can. The traps below are the ones that would silently remove exactly the
combinations a mispricing search cares about.
"""

from __future__ import annotations

import datetime
import json

import pytest

from horseracing_scrape.models import NotYetPublished, ParseError
from horseracing_scrape.parse.exotic_quotes import parse_exotic_quotes
from tests.conftest import real_fixture

RID = "202601010201"
JST = datetime.timezone(datetime.timedelta(hours=9))


def _payload(group: str, grid: dict, official: str | None = "2026-01-01 15:50:17") -> str:
    data: dict = {"odds": {group: grid}}
    if official is not None:
        data["official_datetime"] = official
    return json.dumps({"status": "result", "data": data})


# --- real fixtures ------------------------------------------------------------------------------

def test_quinella_grid_is_the_full_combination_set():
    """66 = C(12,2): every pair is priced, not just the one that won."""
    q = parse_exotic_quotes(real_fixture("exotic_quotes_type4_202601010201.json"),
                            RID, "quinella")
    assert len(q.quotes) == 66
    assert q.quotes[(1, 2)] == (255.8, None, 42)
    assert all(len(k) == 2 and k[0] < k[1] for k in q.quotes), "unordered keys sort ascending"
    assert all(hi is None for _, hi, _ in q.quotes.values()), "quinella is point-priced"


def test_wide_carries_a_real_range():
    w = parse_exotic_quotes(real_fixture("exotic_quotes_type5_202601010201.json"), RID, "wide")
    assert len(w.quotes) == 66
    assert w.quotes[(1, 2)] == (62.1, 65.7, 45)
    assert all(hi is not None and lo <= hi for lo, hi, _ in w.quotes.values())


def test_trio_grid_and_thousands_separator():
    """220 = C(12,3), and '1,024.5' must survive — the comma appears exactly on the long shots."""
    t = parse_exotic_quotes(real_fixture("exotic_quotes_type7_202601010201.json"), RID, "trio")
    assert len(t.quotes) == 220
    assert t.quotes[(1, 2, 3)] == (658.0, None, 107)
    assert t.quotes[(1, 2, 4)] == (1024.5, None, 131)
    assert max(lo for lo, _, _ in t.quotes.values()) > 1000


def test_official_datetime_is_jst():
    q = parse_exotic_quotes(real_fixture("exotic_quotes_type4_202601010201.json"),
                            RID, "quinella")
    assert q.official_at is not None and q.official_at.utcoffset() == datetime.timedelta(hours=9)


# --- key handling -------------------------------------------------------------------------------

def test_ordered_types_keep_finishing_order():
    """馬単 1→2 and 2→1 are different tickets; sorting them would merge two prices into one."""
    e = parse_exotic_quotes(_payload("6", {"0102": ["10.0", "0.0", "3"],
                                           "0201": ["20.0", "0.0", "7"]}), RID, "exacta")
    assert e.quotes[(1, 2)][0] == 10.0
    assert e.quotes[(2, 1)][0] == 20.0


def test_double_digit_numbers_split_on_fixed_width():
    q = parse_exotic_quotes(_payload("4", {"1018": ["5.0", "0.0", "1"]}), RID, "quinella")
    assert list(q.quotes) == [(10, 18)]


def test_wrong_key_width_is_skipped_not_guessed():
    q = parse_exotic_quotes(_payload("4", {"010203": ["5.0", "0.0", "1"],
                                           "0102": ["6.0", "0.0", "2"]}), RID, "quinella")
    assert list(q.quotes) == [(1, 2)]


# --- fail-closed ---------------------------------------------------------------------------------

def test_unpriced_combination_is_omitted_not_stored_as_zero():
    q = parse_exotic_quotes(_payload("4", {"0102": ["---.-", "0.0", ""],
                                           "0103": ["7.5", "0.0", "1"]}), RID, "quinella")
    assert list(q.quotes) == [(1, 3)]


def test_missing_group_fails_closed():
    with pytest.raises(ParseError, match="missing data.odds"):
        parse_exotic_quotes(_payload("7", {"010203": ["5.0", "0.0", "1"]}), RID, "quinella")


def test_empty_grid_fails_closed():
    with pytest.raises(ParseError, match="no usable"):
        parse_exotic_quotes(_payload("4", {"0102": ["---.-", "0.0", ""]}), RID, "quinella")


def test_not_json_fails_closed():
    with pytest.raises(ParseError, match="not valid JSON"):
        parse_exotic_quotes("<html/>", RID, "quinella")


def test_unknown_bet_type_is_a_programming_error():
    with pytest.raises(ValueError, match="no netkeiba odds type"):
        parse_exotic_quotes(_payload("4", {"0102": ["5.0", "0.0", "1"]}), RID, "win")


def test_unsold_pool_is_a_parse_error_not_a_silent_empty():
    """Two days before a race the odds API answers status="NG" / "history odds empty" for the
    combination pools while type=1 already carries win and place prices — the pools simply are not
    on sale yet. That must raise, so the pipeline can classify it as 'not yet' rather than write an
    empty grid; the pipeline turns this specific error into a SKIP, not a failure."""
    payload = json.dumps({"status": "NG", "reason": "history odds empty",
                          "data": {"official_datetime": None, "odds": {}}})
    with pytest.raises(ParseError, match="missing data.odds"):
        parse_exotic_quotes(payload, RID, "quinella")


# --- 「まだ発売前」と「壊れた」の区別 -------------------------------------------------------------
#
# ここが潰れていると、netkeiba がキー名を変えた瞬間に exotic 取得が「未発売」を返し続けて静かに
# 引退し、失敗ジョブが 1 件も出ない。NotYetPublished は ParseError の派生なので、区別を知らない
# 呼び出し元は従来どおり fail-closed のまま。

def test_absent_group_is_not_yet_published_not_breakage():
    """封筒は無事で、この券種のグリッドが無いだけ = 発売前の正常な状態。"""
    payload = json.dumps({"status": "NG", "reason": "history odds empty",
                          "data": {"official_datetime": None, "odds": {}}})
    with pytest.raises(NotYetPublished):
        parse_exotic_quotes(payload, RID, "quinella")


def test_a_missing_envelope_is_breakage_not_not_yet():
    """netkeiba は必ず data.odds を返す。無いなら形が変わった(または別物が返ってきた)。"""
    for payload in (json.dumps({"status": "result", "data": {}}),
                    json.dumps({"status": "result"}),
                    json.dumps({"data": {"odds": []}}),
                    json.dumps([1, 2, 3])):
        with pytest.raises(ParseError) as e:
            parse_exotic_quotes(payload, RID, "quinella")
        assert not isinstance(e.value, NotYetPublished), payload


def test_rows_present_but_unpriced_is_not_yet_published():
    """プールは開いたがまだ金が入っていない — 形は読めている。"""
    with pytest.raises(NotYetPublished, match="unpriced"):
        parse_exotic_quotes(_payload("4", {"0102": ["---.-", "0.0", ""],
                                           "0103": ["---.-", "0.0", ""]}), RID, "quinella")


def test_rows_we_cannot_read_at_all_are_breakage():
    """1 行も形が読めないなら、それは未発売ではなく足元で形式が変わったということ。"""
    with pytest.raises(ParseError) as e:
        parse_exotic_quotes(_payload("4", {"0102": {"odds": "5.0"}}), RID, "quinella")
    assert not isinstance(e.value, NotYetPublished)
    assert "unreadable" in str(e.value)


def test_a_readable_row_still_wins_over_stray_keys():
    """メタデータ的なキーが混ざっていても、読める組み合わせが 1 つでもあれば成功のまま。"""
    q = parse_exotic_quotes(_payload("4", {"updated": "1", "0102": ["5.0", "0.0", "1"]}),
                            RID, "quinella")
    assert list(q.quotes) == [(1, 2)]


def test_not_yet_published_is_still_a_parse_error():
    """区別を知らない呼び出し元は従来どおり fail-closed。"""
    assert issubclass(NotYetPublished, ParseError)

