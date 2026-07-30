"""Exotic portfolio orchestration: build combination probabilities from the win pool.

Selection uses ONLY `race_horses.odds` (market q → 009 engine → combination probabilities, and
010 → estimated combination prices for the inverse-odds staking). Settlement uses the REAL
dividends ingested from netkeiba's payout table.

Pre-registration: docs/plan/prereg-exotic-portfolio.md
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from horseracing_db.enums import BetType, EntryStatus
from horseracing_db.models import ExoticOdds, Race, RaceHorse
from horseracing_db.selection import canonical_selection
from horseracing_eval.exotic_portfolio import (
    DIAGNOSTIC_BET_TYPES,
    PRIMARY_BET_TYPES,
    PortfolioRace,
    evaluate_portfolios,
)
from horseracing_eval.hashing import race_set_hash
from horseracing_probability.engine import joint_probabilities
from horseracing_probability.market_odds import estimate_market_odds
from sqlalchemy import select
from sqlalchemy.orm import Session

EXOTIC_PORTFOLIO_CONTRACT_VERSION = "exotic-portfolio-v1"

SCORED_BET_TYPES = PRIMARY_BET_TYPES + DIAGNOSTIC_BET_TYPES


class ExoticPortfolioError(RuntimeError):
    """Refuse to emit a readout rather than emit a misleading one."""


def _key(bet_type: str, combo) -> tuple[int, ...]:
    """Engine key -> the SAME canonical shape the dividends are stored under.

    The engine keys unordered bet types with a ``frozenset`` (iteration order is arbitrary!) and
    ordered ones with a tuple. Feeding a frozenset straight into ``tuple()`` silently produced
    keys like (10, 2) that never matched the stored (2, 10) — most tickets scored 0 and the
    unordered bet types looked catastrophically unprofitable. Route everything through
    ``canonical_selection``, the same function that wrote the dividend rows: ordered types keep
    finishing order, unordered types sort ascending.
    """
    nums = [int(combo)] if isinstance(combo, str) else [int(x) for x in combo]
    return tuple(canonical_selection(bet_type, nums))


def build_portfolio_races(
    session: Session, *, date_from: datetime.date, date_to: datetime.date,
    bundle_path: Path | None = None,
) -> tuple[dict[str, dict[str, list[PortfolioRace]]], dict[str, int]]:
    """Build the market arm and, when a bundle is supplied, the model arm.

    With a bundle BOTH arms are restricted to the races it covers, so market-vs-model is a paired
    comparison on one population rather than two different ones.
    """
    preds: dict[str, dict] = {}
    if bundle_path is not None:
        preds = json.loads(Path(bundle_path).read_text())["predictions"]

    rows = session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_number, RaceHorse.odds, Race.race_date,
               RaceHorse.horse_id)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to,
               RaceHorse.entry_status == EntryStatus.STARTED)
    ).all()
    by_race: dict[str, list] = {}
    day_of: dict[str, datetime.date] = {}
    for race_id, number, odds, day, horse_id in rows:
        by_race.setdefault(race_id, []).append((number, odds, horse_id))
        day_of[race_id] = day

    div_rows = session.execute(
        select(ExoticOdds.race_id, ExoticOdds.bet_type, ExoticOdds.selection, ExoticOdds.odds)
        .join(Race, Race.race_id == ExoticOdds.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to,
               ExoticOdds.bet_type != BetType.PLACE)
    ).all()
    divs: dict[tuple[str, str], dict[tuple[int, ...], float]] = {}
    for race_id, bet_type, selection, odds in div_rows:
        divs.setdefault((race_id, bet_type), {})[tuple(int(x) for x in selection)] = float(odds)

    sources = ["market"] + (["model"] if preds else [])
    out: dict[str, dict[str, list[PortfolioRace]]] = {
        src: {bt: [] for bt in SCORED_BET_TYPES} for src in sources
    }
    excl = {"no_dividends": 0, "incomplete_odds": 0, "engine_error": 0,
            "not_in_bundle": 0, "bundle_horse_mismatch": 0}
    for race_id, entries in by_race.items():
        if (any(o is None or float(o) <= 0 for _, o, _ in entries)
                or any(n is None for n, _, _ in entries)):
            excl["incomplete_odds"] += 1
            continue
        if not any((race_id, bt) in divs for bt in SCORED_BET_TYPES):
            excl["no_dividends"] += 1
            continue
        numbers = [int(n) for n, _, _ in entries]
        odds_map = {str(n): float(o) for (n, o, _) in entries}
        inv = {n: 1.0 / float(o) for n, o, _ in entries}
        s = sum(inv.values())
        q = {str(n): v / s for n, v in inv.items()}

        p_by_number = None
        if preds:
            pr = preds.get(race_id)
            if pr is None:
                excl["not_in_bundle"] += 1
                continue
            if set(pr) != {h for _, _, h in entries}:
                excl["bundle_horse_mismatch"] += 1
                continue
            raw = {str(n): float(pr[h]["win"]) for n, _, h in entries}
            tot = sum(raw.values())
            if tot <= 0:
                excl["bundle_horse_mismatch"] += 1
                continue
            p_by_number = {k2: v / tot for k2, v in raw.items()}

        try:
            probs_by_source = {"market": joint_probabilities(q, field_size=len(numbers))}
            if p_by_number is not None:
                probs_by_source["model"] = joint_probabilities(
                    p_by_number, field_size=len(numbers))
            est = estimate_market_odds(odds_map, field_size=len(numbers))
        except Exception:  # noqa: BLE001 — one bad race must not abort the pass
            excl["engine_error"] += 1
            continue
        est_by_type = {"wide": est.wide, "quinella": est.quinella, "exacta": est.exacta,
                       "trio": est.trio, "trifecta": est.trifecta}
        if preds and p_by_number is None:
            continue
        for src in sources:
          jp = probs_by_source[src]
          prob_by_type = {"wide": jp.wide, "quinella": jp.quinella, "exacta": jp.exacta,
                          "trio": jp.trio, "trifecta": jp.trifecta}
          for bt in SCORED_BET_TYPES:
            d = divs.get((race_id, bt))
            probs = prob_by_type.get(bt)
            if not d or not probs:
                continue
            prob_keyed = {_key(bt, c): float(v) for c, v in probs.items()}
            # Fail closed on a key-space mismatch: the winning combination MUST be one the engine
            # enumerated. If it is not, every ticket would silently score 0 and the cell would
            # report a fake loss rather than an error (this is exactly how the frozenset bug hid).
            unknown = [c for c in d if c not in prob_keyed]
            if unknown:
                raise ExoticPortfolioError(
                    f"{race_id} {bt}: winning combination {unknown[0]} is not in the engine's "
                    f"{len(prob_keyed)} enumerated combinations — key spaces disagree"
                )
            out[src][bt].append(PortfolioRace(
                race_id=race_id, day=day_of[race_id].isoformat(), bet_type=bt,
                probs=prob_keyed,
                est_odds={_key(bt, c): float(v)
                          for c, v in (est_by_type.get(bt) or {}).items()},
                dividends=d,
            ))
    return out, excl


def run_exotic_portfolio(
    session: Session, *, date_from: datetime.date, date_to: datetime.date,
    seed: int, bootstrap_b: int, bundle_path: Path | None = None,
) -> dict[str, Any]:
    by_src, excl = build_portfolio_races(session, date_from=date_from, date_to=date_to,
                                         bundle_path=bundle_path)
    if not any(any(v.values()) for v in by_src.values()):
        raise ExoticPortfolioError("no eligible races — refusing to emit a readout")
    result = evaluate_portfolios(by_src, b=bootstrap_b, seed=seed)
    any_races = next(v for v in by_src["market"].values() if v)
    return {
        "instrument_contract": {
            "kind": "exotic_portfolio",
            "secondary": True,
            "can_adopt": False,
            "estimand": "return of a WIN-pool-selected combination portfolio settled at REAL "
                        "exotic dividends; 1 - takeout is the no-structure reference",
            "not_measured": [
                "EV against the exotic pool's own PRE-RACE prices — those are not stored, so this "
                "cannot find which individual combinations are cheap",
                "self-impact: our own stake would depress a thin combination pool's dividend",
            ],
            "known_confounds": [
                "selection uses race_horses.odds (closing-leaning) — decision-side lookahead; "
                "the payout side is genuine",
                "67 race-days only; MDE ~0.066 by the §4.2 extrapolation",
            ],
        },
        "provenance": {
            "contract_version": EXOTIC_PORTFOLIO_CONTRACT_VERSION,
            "window": [date_from.isoformat(), date_to.isoformat()],
            "n_races_by_source_bet_type": {
                src: {k: len(v) for k, v in bt.items()} for src, bt in by_src.items()},
            "bundle": str(bundle_path) if bundle_path else None,
            "paired_population": bool(bundle_path),
            "scored_race_set_hash": race_set_hash([r.race_id for r in any_races]),
            "seed": seed, "bootstrap_b": bootstrap_b,
        },
        "exclusions": excl,
        "result": result,
    }
