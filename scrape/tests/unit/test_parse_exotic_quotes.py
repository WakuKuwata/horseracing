"""PRE-RACE exotic price grid parser — the losing combinations are the whole point.

`exotic_odds` only ever holds the dividend of the combination that came in, so it cannot drive
selection. This grid can. The traps below are the ones that would silently remove exactly the
combinations a mispricing search cares about.
"""

from __future__ import annotations

import datetime
import json

import pytest

from horseracing_scrape.models import ParseError
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
