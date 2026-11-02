"""払戻金の解析 — 券種ごとの集合と状態(Feature 176, FR-015).

``parse_refund_block`` is shared with the results page (the result page embeds the same
``refund_unit``). 枠連 is parsed as ``bracket_quinella`` so it can be counted, never stored.
"""

from __future__ import annotations

import datetime
import re

from bs4 import Tag
from horseracing_db.enums import BetType

from ...models import ParseError
from ..models import JraDayPage, JraPayoutBlock, JraPayoutLine, JraPayoutSet, JraRaceBlock
from ._common import nfkc, parse_race_header, soup, text_of, to_int

BRACKET_QUINELLA = "bracket_quinella"
LI_CLASS_TO_BET_TYPE = {
    "win": BetType.WIN,
    "place": BetType.PLACE,
    "wakuren": BRACKET_QUINELLA,
    "umaren": BetType.QUINELLA,
    "umatan": BetType.EXACTA,
    "wide": BetType.WIDE,
    "trio": BetType.TRIO,
    "tierce": BetType.TRIFECTA,
}
#: Ordered types keep the printed order; unordered are sorted (the 011 canonical selection).
_ORDERED = {BetType.EXACTA, BetType.TRIFECTA}
_EXPECTED_ARITY = {
    BetType.WIN: 1,
    BetType.PLACE: 1,
    BRACKET_QUINELLA: 2,
    BetType.QUINELLA: 2,
    BetType.EXACTA: 2,
    BetType.WIDE: 2,
    BetType.TRIO: 3,
    BetType.TRIFECTA: 3,
}
_NUMS_RE = re.compile(r"^\d{1,2}(?:-\d{1,2}){0,2}$")


def _line(div: Tag, bet_type: str) -> JraPayoutLine | None:
    """One ``div.line`` → line, or None for a 返還 (refund) line."""
    num_txt = nfkc(text_of(div.select_one(".num"))).replace(" ", "")
    yen_txt = text_of(div.select_one(".yen"))
    if "返還" in yen_txt or "返還" in num_txt:
        return None
    if not _NUMS_RE.match(num_txt):
        raise ParseError(f"{bet_type}: malformed 組番 {num_txt!r}")
    nums = tuple(int(x) for x in num_txt.split("-"))
    if len(nums) != _EXPECTED_ARITY[bet_type]:
        raise ParseError(f"{bet_type}: {num_txt!r} has {len(nums)} numbers")
    if bet_type not in _ORDERED:
        nums = tuple(sorted(nums))
    yen = to_int(yen_txt)
    if yen is None or yen < 100:
        raise ParseError(f"{bet_type}: payout unreadable {yen_txt!r}")
    pop = to_int(text_of(div.select_one(".pop")))
    return JraPayoutLine(numbers=nums, payout_yen=yen, popularity=pop)


def parse_refund_block(unit: Tag | None) -> JraPayoutSet:
    """``div.refund_unit`` → per-bet-type blocks with state. Missing unit = all not_published."""
    blocks: dict[str, JraPayoutBlock] = {}
    if unit is None:
        return JraPayoutSet(blocks)
    for li in unit.select("li"):
        classes = li.get("class") or []
        bet_type = next(
            (LI_CLASS_TO_BET_TYPE[c] for c in classes if c in LI_CLASS_TO_BET_TYPE), None
        )
        if bet_type is None:
            continue
        dd_text = nfkc(li.get_text(" ", strip=True))
        lines_divs = li.select("div.line")
        if "発売なし" in dd_text or "発売無し" in dd_text:
            blocks[bet_type] = JraPayoutBlock(bet_type, "not_sold")
            continue
        if not lines_divs:
            blocks[bet_type] = JraPayoutBlock(bet_type, "not_published")
            continue
        try:
            lines: list[JraPayoutLine] = []
            refunds: list[int] = []
            for div in lines_divs:
                line = _line(div, bet_type)
                if line is None:
                    n = to_int(text_of(div.select_one(".num")))
                    if n is not None:
                        refunds.append(n)
                    continue
                lines.append(line)
            keys = [ln.numbers for ln in lines]
            if len(keys) != len(set(keys)):
                raise ParseError(f"{bet_type}: duplicate 組番")
            if not lines and refunds:
                blocks[bet_type] = JraPayoutBlock(bet_type, "refund_only", refunds=tuple(refunds))
            elif not lines:
                blocks[bet_type] = JraPayoutBlock(bet_type, "not_published")
            else:
                blocks[bet_type] = JraPayoutBlock(
                    bet_type, "complete", lines=tuple(lines), refunds=tuple(refunds)
                )
        except ParseError as exc:
            blocks[bet_type] = JraPayoutBlock(bet_type, "parse_failed", error=str(exc))
    return JraPayoutSet(blocks)


def parse_payouts_day(html: str, *, expected_date: datetime.date) -> JraDayPage[JraPayoutSet]:
    """払戻金ページ(開催日・1 場): ``li[id^=harai_]`` ごとに header + refund_unit."""
    doc = soup(html)
    units = doc.select("li[id^=harai_]")
    if not units:
        raise ParseError("payouts page: no race units")
    blocks: list[JraRaceBlock[JraPayoutSet]] = []
    venue = kai = nichime = None
    for i, li in enumerate(units, start=1):
        header = li.select_one(".race_header")
        try:
            if header is None:
                raise ParseError("race_header missing")
            race = parse_race_header(header, expected_date=expected_date)
        except ParseError as exc:
            blocks.append(
                JraRaceBlock(race_no=i, race_id=None, state="parse_failed", error=str(exc))
            )
            continue
        if venue is None:
            venue, kai, nichime = race.venue_code, race.kai, race.nichime
        elif (race.venue_code, race.kai, race.nichime) != (venue, kai, nichime):
            blocks.append(
                JraRaceBlock(
                    race_no=race.race_no,
                    race_id=None,
                    state="parse_failed",
                    error="venue/kai/nichime differ from the page's first race",
                )
            )
            continue
        unit = li.select_one(".refund_unit")
        payouts = parse_refund_block(unit)
        if unit is None or not payouts.by_bet_type:
            blocks.append(
                JraRaceBlock(
                    race_no=race.race_no, race_id=race.race_id, state="not_yet", payload=payouts
                )
            )
            continue
        blocks.append(
            JraRaceBlock(
                race_no=race.race_no, race_id=race.race_id, state="published", payload=payouts
            )
        )
    if venue is None:
        raise ParseError("payouts page: every race block failed to parse")
    return JraDayPage(
        race_date=expected_date, venue_code=venue, kai=kai, nichime=nichime, races=tuple(blocks)
    )
