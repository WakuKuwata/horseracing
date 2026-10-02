"""attention router (Feature 138): the 注目条件 S1-S5 — three read-only GETs.

- ``GET /races/{race_id}/attention`` — the race's judged horses (first computation), chip, stages,
  judged vs current values and axis levels. 422 invalid_race_id / 404 race_not_found; a race
  without a first computation is a typed 200 ``unavailable`` (not_computed / odds_unavailable).
- ``GET /attention-rules`` — the frozen registry plus the read-time prospective tally of each rule.
- ``GET /attention/day?date=`` — the chip horses of one race day in post order. ``date`` is
  required (missing or malformed → 422 validation_error); a day without judged horses is 200 with
  ``items=[]``.

All shaping lives in attention.py; the prospective tally is memoised per rule (``TALLY_MEMO``).
"""

from __future__ import annotations

import datetime
import re
from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from horseracing_db.enums import EntryStatus
from horseracing_eval.attention_rules import (
    DISPLAYED_MARKET_EV_MODEL_VERSION,
    SINGLE_SEED_MODEL_VERSION,
)
from sqlalchemy.orm import Session

from ..attention import (
    TALLY_MEMO,
    RuleTally,
    build_attention,
    build_day_items,
    build_rule_tallies,
    build_rules_response,
    unavailable_attention,
)
from ..deps import get_session
from ..queries import (
    attention_checkpoint_rows,
    attention_memo_key,
    attention_picks_for_date,
    attention_picks_for_race,
    attention_scan_for_race,
    attention_scans_for_races,
    attention_started_by_race,
    attention_tally_rows,
    entries_for_races,
    get_race,
    market_ev_rows,
    market_ev_rows_for_races,
    race_has_results,
    race_ids_with_results,
    races_by_id,
)
from ..schemas import AttentionDayResponse, AttentionResponse, AttentionRulesResponse

router = APIRouter()

_RACE_ID = re.compile(r"^[0-9]{12}$")


def _err(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"status": status, "code": code, "detail": detail}
    )


def rule_tallies(session: Session) -> dict[str, RuleTally]:
    """All five rules' prospective tallies, recomputing only the rules whose memo key moved."""

    def load(rule_ids: list[str]) -> dict[str, RuleTally]:
        return build_rule_tallies(
            rule_ids,
            attention_tally_rows(session, rule_ids),
            attention_started_by_race(session, rule_ids),
            attention_checkpoint_rows(session),
        )

    return TALLY_MEMO.get(attention_memo_key(session), load)


@router.get(
    "/races/{race_id}/attention", response_model=AttentionResponse, tags=["attention"]
)
def race_attention(race_id: str, session: Session = Depends(get_session)):
    if not _RACE_ID.match(race_id):
        return _err(422, "invalid_race_id", "race_id must be 12 digits")
    race = get_race(session, race_id)
    if race is None:
        return _err(404, "race_not_found", f"race {race_id} not found")
    scan = attention_scan_for_race(session, race_id)
    entries = entries_for_races(session, [race_id])
    if scan is None:
        # no first computation (most future races): answer from the entries alone — no picks,
        # market-ev rows, memo-key query or tally recompute for a race that has nothing to show
        return unavailable_attention(race_id, entries)
    return build_attention(
        race_id=race_id,
        post_time=race.post_time,
        has_results=race_has_results(session, race_id),
        scan=scan,
        picks=attention_picks_for_race(session, race_id),
        entries=entries,
        ens_rows=market_ev_rows(session, race_id, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION),
        single_rows=market_ev_rows(session, race_id, model_version=SINGLE_SEED_MODEL_VERSION),
        tallies=rule_tallies(session),
    )


@router.get("/attention-rules", response_model=AttentionRulesResponse, tags=["attention"])
def attention_rule_list(session: Session = Depends(get_session)):
    return build_rules_response(rule_tallies(session))


@router.get("/attention/day", response_model=AttentionDayResponse, tags=["attention"])
def attention_day(date: datetime.date, session: Session = Depends(get_session)):
    picks = attention_picks_for_date(session, date)
    race_ids = sorted({p.race_id for p in picks})
    if not race_ids:
        return AttentionDayResponse(date=date, items=[])
    tallies = rule_tallies(session)
    races = races_by_id(session, race_ids)
    scans = attention_scans_for_races(session, race_ids)
    with_results = race_ids_with_results(session, race_ids)

    picks_by_race = defaultdict(list)
    for p in picks:
        picks_by_race[p.race_id].append(p)
    entries_by_race = defaultdict(list)
    names: dict[tuple[str, str], str | None] = {}
    entries = entries_for_races(session, race_ids)
    for e in entries:
        entries_by_race[e.race_id].append(e)
        names[(e.race_id, e.horse_id)] = e.horse_name
    non_starters = frozenset(
        (e.race_id, e.horse_id) for e in entries if e.entry_status in EntryStatus.NON_STARTERS
    )
    ens_by_race = defaultdict(list)
    for row in market_ev_rows_for_races(
        session, race_ids, model_version=DISPLAYED_MARKET_EV_MODEL_VERSION
    ):
        ens_by_race[row.race_id].append(row)
    single_by_race = defaultdict(list)
    for row in market_ev_rows_for_races(
        session, race_ids, model_version=SINGLE_SEED_MODEL_VERSION
    ):
        single_by_race[row.race_id].append(row)

    responses = {
        race_id: build_attention(
            race_id=race_id,
            post_time=races[race_id].post_time,
            has_results=race_id in with_results,
            scan=scans.get(race_id),
            picks=picks_by_race[race_id],
            entries=entries_by_race[race_id],
            ens_rows=ens_by_race[race_id],
            single_rows=single_by_race[race_id],
            tallies=tallies,
        )
        for race_id in race_ids
    }
    return build_day_items(date=date, races=races, responses=responses, horse_names=names,
                           non_starters=non_starters)
