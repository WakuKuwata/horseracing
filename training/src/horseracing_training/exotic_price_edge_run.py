"""Orchestration for the real-price exotic edge test.

Joins three sources that only recently existed together:
  * the WIN pool (race_horses.odds -> q -> 009 engine) for the probability,
  * `exotic_quotes` for the exotic pool's OWN price of every combination, and
  * `exotic_odds` for what actually paid.

Fails closed on a key-space mismatch. The engine keys unordered bet types with a frozenset, whose
iteration order is arbitrary; a mismatch there once made every ticket score 0 and reported it as a
catastrophic loss rather than an error, so both the price grid and the dividends are checked
against the engine's enumeration before anything is scored.

Pre-registration: docs/plan/prereg-exotic-real-price-edge.md
"""

from __future__ import annotations

import datetime
from typing import Any

from horseracing_db.enums import EntryStatus
from horseracing_db.models import ExoticOdds, ExoticQuote, Race, RaceHorse, RaceResult
from horseracing_db.selection import canonical_selection
from horseracing_eval.exotic_price_edge import BET_TYPES, PriceEdgeRace, evaluate
from horseracing_eval.hashing import race_set_hash
from horseracing_probability.engine import joint_probabilities
from horseracing_probability.market_odds import default_market_stage_discount
from sqlalchemy import select
from sqlalchemy.orm import Session

CONTRACT_VERSION = "exotic-price-edge-v1"


class ExoticPriceEdgeError(RuntimeError):
    """Refuse to emit a readout rather than emit a misleading one."""


def _key(bet_type: str, combo) -> tuple[int, ...]:
    nums = [int(combo)] if isinstance(combo, str) else [int(x) for x in combo]
    return tuple(canonical_selection(bet_type, nums))


def build(session: Session) -> tuple[dict[str, list[PriceEdgeRace]], dict[str, int]]:
    entries = session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_number, RaceHorse.odds, Race.race_date)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED,
               RaceHorse.race_id.in_(select(ExoticQuote.race_id).distinct()))
    ).all()
    by_race: dict[str, list] = {}
    day_of: dict[str, datetime.date] = {}
    for race_id, number, odds, day in entries:
        by_race.setdefault(race_id, []).append((number, odds))
        day_of[race_id] = day

    grids: dict[tuple[str, str], dict] = {}
    for race_id, bet_type, quotes in session.execute(
        select(ExoticQuote.race_id, ExoticQuote.bet_type, ExoticQuote.quotes)
    ):
        if bet_type in BET_TYPES:
            grids[(race_id, bet_type)] = quotes

    divs: dict[tuple[str, str], dict[tuple[int, ...], float]] = {}
    for race_id, bet_type, selection, odds in session.execute(
        select(ExoticOdds.race_id, ExoticOdds.bet_type, ExoticOdds.selection, ExoticOdds.odds)
        .where(ExoticOdds.bet_type.in_(BET_TYPES))
    ):
        divs.setdefault((race_id, bet_type), {})[tuple(int(x) for x in selection)] = float(odds)

    winners: dict[str, set[int]] = {}
    for race_id, number in session.execute(
        select(RaceResult.race_id, RaceHorse.horse_number)
        .join(RaceHorse, (RaceHorse.race_id == RaceResult.race_id)
              & (RaceHorse.horse_id == RaceResult.horse_id))
        .where(RaceResult.finish_order == 1)
    ):
        if number is not None:
            winners.setdefault(race_id, set()).add(int(number))

    sd = default_market_stage_discount()
    out: dict[str, list[PriceEdgeRace]] = {bt: [] for bt in BET_TYPES}
    excl = {"incomplete_odds": 0, "no_dividends": 0, "no_winner": 0, "engine_error": 0,
            "no_priceable_combinations": 0}
    dropped_scratched = 0

    for race_id, ents in by_race.items():
        if any(o is None or float(o) <= 0 or n is None for n, o in ents):
            excl["incomplete_odds"] += 1
            continue
        if race_id not in winners:
            excl["no_winner"] += 1
            continue
        numbers = [int(n) for n, _ in ents]
        inv = {str(n): 1.0 / float(o) for n, o in ents}
        s = sum(inv.values())
        q = {k: v / s for k, v in inv.items()}
        try:
            jp = joint_probabilities(q, field_size=len(numbers), stage_discount=sd)
        except Exception:  # noqa: BLE001 — one bad race must not abort the pass
            excl["engine_error"] += 1
            continue
        tables = {"quinella": jp.quinella, "wide": jp.wide, "trio": jp.trio}

        for bt in BET_TYPES:
            grid = grids.get((race_id, bt))
            d = divs.get((race_id, bt))
            if not grid:
                continue
            if not d:
                excl["no_dividends"] += 1
                continue
            prob = {_key(bt, c): float(v) for c, v in tables[bt].items()}
            # Wide publishes a range; the LOW end is the conservative price (pre-registered).
            raw_price = {tuple(int(x) for x in k.split("-")): float(v[0])
                         for k, v in grid.items()}
            # A grid is captured from the entry list and can predate a scratch, so it legitimately
            # prices combinations that never became bettable. Those are refunded, not lost — drop
            # them (counted) rather than treating a normal cancellation as a key-space fault. This
            # is the ordinary case for forward capture: the grid is taken before the race, and
            # scratches land afterwards.
            started = set(numbers)
            price = {c: o for c, o in raw_price.items() if started.issuperset(c)}
            dropped_scratched += len(raw_price) - len(price)
            if not price:
                excl["no_priceable_combinations"] += 1
                continue
            # The DIVIDEND side stays strict: a paid combination can only involve horses that ran,
            # so anything unenumerable here is a genuine integrity fault (this is the check that
            # caught the frozenset key bug).
            unknown = [c for c in d if c not in prob]
            if unknown:
                raise ExoticPriceEdgeError(
                    f"{race_id} {bt}: paid combination {unknown[0]} is absent from the engine's "
                    f"{len(prob)} enumerated combinations — key spaces disagree"
                )
            out[bt].append(PriceEdgeRace(
                race_id=race_id, day=day_of[race_id].isoformat(), bet_type=bt,
                prob=prob, price=price, dividends=d,
            ))
    excl["price_rows_dropped_scratched"] = dropped_scratched
    return out, excl


def run(session: Session, *, seed: int, bootstrap_b: int) -> dict[str, Any]:
    by_bt, excl = build(session)
    if not any(by_bt.values()):
        raise ExoticPriceEdgeError("no eligible races — refusing to emit a readout")
    result = evaluate(by_bt, b=bootstrap_b, seed=seed)
    any_races = next(v for v in by_bt.values() if v)
    return {
        "instrument_contract": {
            "kind": "exotic_price_edge",
            "secondary": True,
            "can_adopt": False,
            "estimand": "return of buying combinations the exotic pool prices below what the WIN "
                        "pool implies (EV = P_market x O_real), settled at the real dividend",
            "known_confounds": [
                "the captured grids are FINAL odds of settled races — selection carries the same "
                "closing lookahead as every earlier measurement, and it may bite harder here "
                "because late money moves combination prices more than win prices",
                "self-impact is not measurable: our own stake would depress a thin pool's dividend",
                "the win-pool derivation still under-prices the 1000x+ band (0.70-0.85), so a high "
                "EV there may be OUR error rather than the market's — read band_mix",
            ],
        },
        "provenance": {
            "contract_version": CONTRACT_VERSION,
            "n_races_by_bet_type": {k: len(v) for k, v in by_bt.items()},
            "scored_race_set_hash": race_set_hash([r.race_id for r in any_races]),
            "stage_discount": "market default (see probability.market_odds)",
            "seed": seed, "bootstrap_b": bootstrap_b,
        },
        "exclusions": excl,
        "result": result,
    }
