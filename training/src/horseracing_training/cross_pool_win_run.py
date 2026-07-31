"""Orchestration for betting the WIN pool on the EXOTIC pool's opinion.

Shares its population construction with ``pool_information_run`` on purpose: the same races, the
same devig, the same scratch handling. A policy measured on a different population than the
information finding would not be testing that finding.

Pre-registration: docs/plan/prereg-cross-pool-win.md
"""

from __future__ import annotations

from typing import Any

import numpy as np
from horseracing_eval.cross_pool_win import CrossPoolWinRace, evaluate
from horseracing_eval.hashing import race_set_hash
from sqlalchemy.orm import Session

from .pool_information_run import build as build_pool_races

CONTRACT_VERSION = "cross-pool-win-v1"


class CrossPoolWinError(RuntimeError):
    """Refuse to emit a readout rather than emit a misleading one."""


def run(session: Session, *, seed: int, bootstrap_b: int) -> dict[str, Any]:
    # pool_information_run returns DeltaR2Race (p = exotic marginal, q = win pool). The win ODDS
    # are recoverable from q exactly: q_i = (1/O_i)/Σ(1/O_j), so O_i = 1/(q_i * Σ(1/O_j)). Rather
    # than invert, re-read the odds so the settlement price is the stored value, not a derivation.
    from horseracing_db.enums import EntryStatus
    from horseracing_db.models import RaceHorse
    from sqlalchemy import select

    base, excl = build_pool_races(session)
    if not base:
        raise CrossPoolWinError("no eligible races — refusing to emit a readout")

    ids = [r.race_id for r in base]
    odds_by_race: dict[str, dict[int, float]] = {}
    for race_id, number, odds in session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_number, RaceHorse.odds)
        .where(RaceHorse.entry_status == EntryStatus.STARTED, RaceHorse.race_id.in_(ids))
    ):
        if number is not None and odds is not None:
            odds_by_race.setdefault(race_id, {})[int(number)] = float(odds)

    races: list[CrossPoolWinRace] = []
    for r in base:
        # pool_information_run orders horses by 馬番 ascending; rebuild the same order.
        nums = sorted(odds_by_race.get(r.race_id, {}))
        if len(nums) != r.p.size:
            excl["odds_field_mismatch"] = excl.get("odds_field_mismatch", 0) + 1
            continue
        races.append(CrossPoolWinRace(
            race_id=r.race_id, day=r.day, x=r.p, q=r.q,
            odds=np.array([odds_by_race[r.race_id][n] for n in nums], dtype=float),
            winner_idx=r.winner_idx,
        ))
    if not races:
        raise CrossPoolWinError("no scoreable races after joining win odds")

    result = evaluate(races, b=bootstrap_b, seed=seed)
    return {
        "instrument_contract": {
            "kind": "cross_pool_win",
            "secondary": True,
            "can_adopt": False,
            "estimand": "return of backing horses the EXOTIC pool rates above the WIN pool, "
                        "settled at the real win odds",
            "known_confounds": [
                "both signals are closing prices: the settlement side is right (pari-mutuel pays "
                "the final odds) but the selection side carries lookahead",
                "x is an 'in the top 2' quantity while the bet is on winning — the blend absorbed "
                "that scale difference, a ratio threshold does not",
                "self-impact is not measurable",
            ],
        },
        "provenance": {
            "contract_version": CONTRACT_VERSION,
            "n_races": len(races),
            "n_days": len({r.day for r in races}),
            "scored_race_set_hash": race_set_hash([r.race_id for r in races]),
            "seed": seed, "bootstrap_b": bootstrap_b,
        },
        "exclusions": excl,
        "result": result,
    }
