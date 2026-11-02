"""176 T069: 払戻金ページの解析 — bet-type blocks as sets with a state."""

from __future__ import annotations

import datetime

from horseracing_scrape.jra.parse._common import soup
from horseracing_scrape.jra.parse.payouts import (
    BRACKET_QUINELLA,
    parse_payouts_day,
    parse_refund_block,
)
from tests.jra_fixtures import jra_text

D = datetime.date(2026, 10, 31)
FIX = "payouts_tokyo_20261031.sjis.html"


def test_day_page_twelve_races_seven_types_plus_bracket():
    page = parse_payouts_day(jra_text(FIX), expected_date=D)
    assert page.state_counts()["races_published"] == 12
    r1 = page.races[0].payload
    assert set(r1.by_bet_type) == {
        "win",
        "place",
        BRACKET_QUINELLA,
        "quinella",
        "exacta",
        "wide",
        "trio",
        "trifecta",
    }
    assert all(b.state == "complete" for b in r1.by_bet_type.values())
    place = r1.block("place")
    assert [(ln.numbers[0], ln.payout_yen, ln.popularity) for ln in place.lines] == [
        (12, 150, 2),
        (11, 100, 1),
        (6, 510, 7),
    ]
    wide = r1.block("wide")
    assert sorted(ln.numbers for ln in wide.lines) == [(6, 11), (6, 12), (11, 12)]
    assert next(ln.payout_yen for ln in wide.lines if ln.numbers == (6, 12)) == 2320  # "2,320円"
    assert r1.block("exacta").lines[0].numbers == (12, 11)  # ordered type keeps printed order


def test_duplicate_combination_fails_only_that_bet_type():
    page = parse_payouts_day(jra_text("derived/payouts_duplicate_combo.sjis.html"), expected_date=D)
    r1 = page.races[0].payload
    assert r1.block("trio").state == "parse_failed"
    assert r1.block("win").state == "complete" and r1.block("trifecta").state == "complete"
    assert page.races[0].state == "published"


def _unit(inner: str):
    return soup(f"<div class='refund_unit'><ul>{inner}</ul></div>").select_one(".refund_unit")


def test_block_states():
    not_sold = _unit("<li class='wide'><dl><dt>ワイド</dt><dd>発売なし</dd></dl></li>")
    assert parse_refund_block(not_sold).block("wide").state == "not_sold"
    refund_only = _unit(
        "<li class='win'><dl><dt>単勝</dt><dd><div class='line'><div class='num'>5</div>"
        "<div class='yen'>返還</div></div></dd></dl></li>"
    )
    b = parse_refund_block(refund_only).block("win")
    assert b.state == "refund_only" and b.refunds == (5,)
    assert parse_refund_block(None).block("trio").state == "not_published"
    empty = _unit("<li class='trio'><dl><dt>3連複</dt><dd></dd></dl></li>")
    assert parse_refund_block(empty).block("trio").state == "not_published"
    bad = _unit(
        "<li class='trio'><dl><dt>3連複</dt><dd><div class='line'><div class='num'>1-2</div>"
        "<div class='yen'>500円</div></div></dd></dl></li>"
    )
    assert parse_refund_block(bad).block("trio").state == "parse_failed"  # arity 2 for a trio
    dead_heat = _unit(
        "<li class='win'><dl><dt>単勝</dt><dd><div class='line'><div class='num'>3</div>"
        "<div class='yen'>460円</div></div><div class='line'><div class='num'>7</div>"
        "<div class='yen'>380円</div></div></dd></dl></li>"
    )
    lines = parse_refund_block(dead_heat).block("win").lines
    assert [(ln.numbers, ln.payout_yen) for ln in lines] == [((3,), 460), ((7,), 380)]
