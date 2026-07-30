"""Cross-pool (win → place) orchestration: build PlaceRace inputs from the DB.

Selection uses the WIN pool only (`q` from `race_horses.odds`, complete field); settlement uses the
REAL place dividends ingested from netkeiba's payout table. No result join is needed — a horse
absent from the payout table did not place.

Honest caveats, carried into the payload:
* `race_horses.odds` is closing-leaning, so selection has lookahead. The payout side is genuine.
* Only 2025-11 onward has real place dividends (the cache backfill window), which is 67 race-days.
"""

from __future__ import annotations

import datetime
from typing import Any

from horseracing_db.enums import BetType, EntryStatus
from horseracing_db.models import ExoticOdds, Race, RaceHorse, RaceResult
from horseracing_eval.cross_pool import PlaceRace, evaluate_cross_pool, paired_win_vs_place
from horseracing_eval.hashing import race_set_hash
from horseracing_eval.stage_discount import StageDiscount
from horseracing_probability.engine import joint_probabilities
from sqlalchemy import select
from sqlalchemy.orm import Session

CROSS_POOL_CONTRACT_VERSION = "cross-pool-v1"


def expected_place_rows(field_size: int) -> int:
    """JRA pays place on top-3 for 8+ starters, top-2 for 5-7, and nothing for <=4."""
    if field_size <= 4:
        return 0
    return 2 if field_size <= 7 else 3


class CrossPoolError(RuntimeError):
    """Refuse to emit a readout rather than emit a misleading one."""


def _q_from_odds(odds: list[float]) -> list[float]:
    inv = [1.0 / o for o in odds]
    s = sum(inv)
    return [x / s for x in inv]


def build_place_races(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    lambda2: float | None,
    lambda3: float | None,
) -> tuple[list[PlaceRace], dict[str, int]]:
    rows = session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_number, RaceHorse.odds, Race.race_date)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to,
               RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    by_race: dict[str, list] = {}
    day_of: dict[str, datetime.date] = {}
    for race_id, number, odds, day in rows:
        by_race.setdefault(race_id, []).append((number, odds))
        day_of[race_id] = day

    div_rows = session.execute(
        select(ExoticOdds.race_id, ExoticOdds.selection, ExoticOdds.odds)
        .join(Race, Race.race_id == ExoticOdds.race_id)
        .where(ExoticOdds.bet_type == BetType.PLACE,
               Race.race_date >= date_from, Race.race_date <= date_to)
    ).all()
    divs: dict[str, dict[int, float]] = {}
    for race_id, selection, odds in div_rows:
        if len(selection) != 1:
            continue
        divs.setdefault(race_id, {})[int(selection[0])] = float(odds)

    # 1着馬 (dead heats give several). The place side is settled from the payout table, so the
    # two sources must agree: a winner ALWAYS places, and a winner missing from the place
    # dividends means the payout table was not fully ingested for that race.
    winner_rows = session.execute(
        select(RaceResult.race_id, RaceHorse.horse_number)
        .join(RaceHorse, (RaceHorse.race_id == RaceResult.race_id)
              & (RaceHorse.horse_id == RaceResult.horse_id))
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(RaceResult.finish_order == 1,
               Race.race_date >= date_from, Race.race_date <= date_to)
    ).all()
    winners: dict[str, set[int]] = {}
    for race_id, number in winner_rows:
        if number is not None:
            winners.setdefault(race_id, set()).add(int(number))

    sd = (StageDiscount(lambda2=lambda2, lambda3=lambda3)
          if lambda2 is not None and lambda3 is not None else None)

    out: list[PlaceRace] = []
    excl = {"no_dividends": 0, "incomplete_odds": 0, "no_place_bet": 0,
            "dividend_count_mismatch": 0, "engine_no_place": 0,
            "no_winner": 0, "winner_not_in_place_dividends": 0}
    for race_id, entries in by_race.items():
        d = divs.get(race_id)
        if not d:
            excl["no_dividends"] += 1
            continue
        if (any(o is None or float(o) <= 0 for _, o in entries)
                or any(n is None for n, _ in entries)):
            excl["incomplete_odds"] += 1
            continue
        field_size = len(entries)
        if expected_place_rows(field_size) == 0:
            excl["no_place_bet"] += 1
            continue
        # Dead heats legitimately add rows; FEWER rows than the field-size rule means the payout
        # table was not fully ingested, and scoring it would count real placings as misses.
        if len(d) < expected_place_rows(field_size):
            excl["dividend_count_mismatch"] += 1
            continue

        win_set = winners.get(race_id)
        if not win_set:
            excl["no_winner"] += 1
            continue
        if not win_set.issubset(d):
            # A winner always places — its absence means the payout table is incomplete, and
            # scoring it would silently count a real placing as a miss.
            excl["winner_not_in_place_dividends"] += 1
            continue

        numbers = [int(n) for n, _ in entries]
        q = _q_from_odds([float(o) for _, o in entries])
        jp = joint_probabilities({str(n): qi for n, qi in zip(numbers, q, strict=True)},
                                 field_size=field_size, stage_discount=sd)
        if jp.place is None:
            excl["engine_no_place"] += 1
            continue
        order = sorted(range(field_size), key=lambda i: (-q[i], numbers[i]))
        out.append(PlaceRace(
            race_id=race_id,
            day=day_of[race_id].isoformat(),
            field_size=field_size,
            numbers=tuple(numbers[i] for i in order),
            q=tuple(q[i] for i in order),
            place_prob=tuple(float(jp.place[str(numbers[i])]) for i in order),
            dividends=d,
            win_odds={int(n): float(o) for n, o in entries},
            winner_numbers=frozenset(win_set),
        ))
    return out, excl


def run_cross_pool(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    seed: int,
    bootstrap_b: int,
    lambda2: float | None,
    lambda3: float | None,
) -> dict[str, Any]:
    races, excl = build_place_races(session, date_from=date_from, date_to=date_to,
                                    lambda2=lambda2, lambda3=lambda3)
    if not races:
        raise CrossPoolError("no eligible races — refusing to emit a readout")
    result = evaluate_cross_pool(races, b=bootstrap_b, seed=seed)
    result["paired_win_vs_place"] = paired_win_vs_place(races, b=bootstrap_b, seed=seed)
    return {
        "instrument_contract": {
            "kind": "cross_pool_place",
            "secondary": True,
            "can_adopt": False,
            "estimand": "return of a WIN-pool-selected place policy settled at REAL place "
                        "dividends; 1 - takeout = 0.80 is the no-structure reference",
            "primary_readout": "rank_profile (λ-invariant)",
            "not_measured": [
                "EV = P_place x O_place selection — needs PRE-RACE place pool prices, which are "
                "not stored (only dividends, i.e. post-race payouts of horses that placed)",
                "which individual place prices are cheap (Dr.Z proper needs place bet fractions)",
            ],
            "known_confounds": [
                "selection uses race_horses.odds (closing-leaning) — decision-side lookahead; "
                "the payout side is genuine",
                "dividends exist only from the 2025-11 cache-backfill window onward",
            ],
        },
        "provenance": {
            "contract_version": CROSS_POOL_CONTRACT_VERSION,
            "window": [date_from.isoformat(), date_to.isoformat()],
            "scored_race_set_hash": race_set_hash([r.race_id for r in races]),
            "stage_discount": {"lambda2": lambda2, "lambda3": lambda3},
            "seed": seed,
            "bootstrap_b": bootstrap_b,
        },
        "exclusions": excl,
        "result": result,
    }
