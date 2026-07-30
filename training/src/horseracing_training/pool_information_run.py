"""Does the EXOTIC pool know anything the WIN pool does not?

Every earlier measurement compared OUR derived probability against a market. That leaves two very
different explanations tangled together when the market wins:

  1. the Plackett-Luce derivation from win odds is lossy, or
  2. the people betting combinations hold information the win pool has not absorbed.

Only the second leaves a route to profit, and the two are separated by dropping our derivation
entirely: take the signal straight out of the real 馬連 grid and ask whether it adds anything to
the win pool as a predictor of the WINNER.

    x_i = Σ_{j≠i} devig(1/O_ij)      the exotic pool's own marginal for horse i

Both sides are closing-time market prices on the same races, so the comparison is fair, and no
outcome is read to build either. ``x`` is deliberately NOT required to be a calibrated win
probability: the two-stage blend of ``horseracing_eval.delta_r2`` absorbs scale, so a
misspecified PL cannot masquerade as "the exotic pool is uninformative".

Read the BLEND WEIGHT, not just ΔR². For the OOF model the fitted α on its own signal is
0.0004–0.005 — the market simply ignores it. A materially non-zero α here would mean the two pools
are near-peer information sources even if ΔR² is too small to call.
"""

from __future__ import annotations

import collections
from typing import Any

import numpy as np
from horseracing_db.enums import BetType, EntryStatus
from horseracing_db.models import ExoticQuote, Race, RaceHorse, RaceResult
from horseracing_eval.delta_r2 import DeltaR2Race, evaluate_delta_r2
from horseracing_eval.hashing import race_set_hash
from sqlalchemy import select
from sqlalchemy.orm import Session

CONTRACT_VERSION = "pool-information-v1"

#: Prequential block. Monthly gives ~19 blocks over the current window — enough that the first
#: (fit-only) block costs little, while each block still holds enough races to fit two coefficients.
BLOCK_FORMAT = "%Y-%m"


class PoolInformationError(RuntimeError):
    """Refuse to emit a readout rather than emit a misleading one."""


def build(session: Session) -> tuple[list[DeltaR2Race], dict[str, int]]:
    grids = dict(session.execute(
        select(ExoticQuote.race_id, ExoticQuote.quotes)
        .where(ExoticQuote.bet_type == BetType.QUINELLA)
    ).all())

    by_race: dict[str, list[tuple[int, float]]] = {}
    day_of: dict[str, Any] = {}
    for race_id, number, odds, day in session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_number, RaceHorse.odds, Race.race_date)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(RaceHorse.entry_status == EntryStatus.STARTED,
               RaceHorse.race_id.in_(list(grids)))
    ):
        if number is None or odds is None or float(odds) <= 0:
            by_race.setdefault(race_id, []).append((-1, -1.0))   # marks the race unusable
            continue
        by_race.setdefault(race_id, []).append((int(number), float(odds)))
        day_of[race_id] = day

    winners: dict[str, set[int]] = {}
    for race_id, number in session.execute(
        select(RaceResult.race_id, RaceHorse.horse_number)
        .join(RaceHorse, (RaceHorse.race_id == RaceResult.race_id)
              & (RaceHorse.horse_id == RaceResult.horse_id))
        .where(RaceResult.finish_order == 1, RaceResult.race_id.in_(list(grids)))
    ):
        if number is not None:
            winners.setdefault(race_id, set()).add(int(number))

    races: list[DeltaR2Race] = []
    excl: collections.Counter = collections.Counter()
    for race_id, grid in grids.items():
        ents = sorted(by_race.get(race_id, []))
        if not ents or any(n < 0 for n, _ in ents):
            excl["incomplete_win_odds"] += 1
            continue
        win = winners.get(race_id)
        if not win:
            excl["no_winner"] += 1
            continue
        if len(win) > 1:
            excl["dead_heat"] += 1          # winner NLL is defined on a single winner
            continue
        nums = [n for n, _ in ents]
        started = set(nums)
        if not started.issuperset(win):
            excl["winner_not_started"] += 1
            continue

        # The grid is captured from the entry list and may predate a scratch, so restrict it to
        # combinations that stayed bettable before deviging — otherwise the refunded ones would
        # take a share of the implied probability mass.
        pairs = {tuple(int(x) for x in k.split("-")): float(v[0]) for k, v in grid.items()}
        pairs = {c: o for c, o in pairs.items() if started.issuperset(c) and o > 0}
        if len(pairs) < len(nums):
            excl["thin_grid"] += 1          # too few priced pairs to marginalise meaningfully
            continue

        inv = {c: 1.0 / o for c, o in pairs.items()}
        total = sum(inv.values())
        marg = dict.fromkeys(nums, 0.0)
        for (a, b), v in inv.items():
            marg[a] += v / total
            marg[b] += v / total
        x = np.array([marg[n] for n in nums], dtype=float)
        if x.sum() <= 0:
            excl["degenerate_marginal"] += 1
            continue
        x = x / x.sum()

        qi = np.array([1.0 / o for _, o in ents], dtype=float)
        q = qi / qi.sum()

        races.append(DeltaR2Race(
            race_id=race_id, day=day_of[race_id].isoformat(),
            block=day_of[race_id].strftime(BLOCK_FORMAT),
            winner_idx=nums.index(next(iter(win))), p=x, q=q,
        ))
    return races, dict(excl)


def run(session: Session, *, seed: int, bootstrap_b: int) -> dict[str, Any]:
    races, excl = build(session)
    if not races:
        raise PoolInformationError("no eligible races — refusing to emit a readout")
    rep = evaluate_delta_r2(races, b=bootstrap_b, seed=seed)
    return {
        "instrument_contract": {
            "kind": "pool_information",
            "secondary": True,
            "can_adopt": False,
            "estimand": "pseudo-R2 increment of the EXOTIC pool's own 馬連 marginal over the WIN "
                        "pool, as a predictor of the winner",
            "why": "separates 'our PL derivation is lossy' from 'combination bettors know "
                   "something the win pool has not absorbed' — only the second leaves a route",
            "read_the_blend_weight": "alpha ~0.0004-0.005 for the OOF model means the market "
                                     "ignores it; a materially non-zero alpha here means the two "
                                     "pools are near-peer sources even when delta-R2 is too small "
                                     "to call",
            "known_confounds": [
                "both signals are closing-time prices, so this says nothing about what was "
                "knowable at a decision time earlier than the close",
                "the marginal is 'in the top 2', not 'wins' — it is a related but different "
                "quantity, which the blend's scaling absorbs but which limits interpretation",
            ],
        },
        "provenance": {
            "contract_version": CONTRACT_VERSION,
            "n_races": len(races),
            "n_days": len({r.day for r in races}),
            "n_blocks": len({r.block for r in races}),
            "block_format": BLOCK_FORMAT,
            "scored_race_set_hash": race_set_hash([r.race_id for r in races]),
            "seed": seed, "bootstrap_b": bootstrap_b,
        },
        "exclusions": excl,
        "result": rep.to_dict(),
    }
