"""Feature 049: retrospective WIN backtest — pure, read-only, betting-independent.

Given a persisted WIN recommendation and the race's official finishing map, report whether the
recommended horse actually WON and the REAL-odds return. WIN is the only bet type valued here:
it uses real single-win odds (race_horses.odds → market_odds_used, is_estimated_odds=False,
Feature 045), so the realised return is genuine (NOT pseudo, NOT double-pseudo).

This mirrors the semantics of betting/roi.py ``score_backtest`` (hit = recommended horse in the
winner set; a started-but-not-1st horse is a loss) but is a single definitional predicate
(finish_order == 1) reimplemented here so the read-only API never imports the write-path
``betting`` package (constitution VI boundary). No results value ever flows back into model
features (constitution II leak boundary) — this is display-only, computed at read time.
"""

from __future__ import annotations

import dataclasses

from horseracing_db.enums import ResultStatus


@dataclasses.dataclass(frozen=True)
class WinRealized:
    """Retrospective outcome of one WIN recommendation. All-null when unsettled / not a win row."""

    settled: bool = False
    hit: bool | None = None
    dead_heat: bool = False
    realized_return: float | None = None  # per-unit payout multiple: real odds if hit else 0.0
    realized_roi: float | None = None  # realized_return - 1 (odds-1 on hit, -1 on miss)


_UNSETTLED = WinRealized()


def win_realized(
    selection,
    market_odds_used,
    *,
    finish_map: dict[str, tuple[int | None, str]],
    n_winners: int,
) -> WinRealized:
    """Compute the realised WIN outcome for one recommendation (pure).

    ``selection`` is the raw persisted win selection ``{"horse_id", "horse_number"}`` (007).
    ``finish_map`` maps horse_id → (finish_order, result_status) for every result row of the race;
    empty ⇒ the race has no official result yet (unsettled). ``n_winners`` is the count of
    finished horses at finish_order==1 (>1 ⇒ dead heat, whose real dividend is split).

    Cases (settled only): selected horse absent from results ⇒ void (hit=None, no return);
    finished & finish_order==1 ⇒ hit (return=real odds, dead_heat if n_winners>1);
    otherwise (finished non-1st, stopped, disqualified) ⇒ miss (return=0.0, roi=-1.0).
    """
    if not finish_map:  # no official result → unsettled
        return _UNSETTLED
    if not isinstance(selection, dict):  # not a win row — realised valuation is win-only
        return WinRealized(settled=True)
    horse_id = selection.get("horse_id")
    entry = finish_map.get(horse_id) if horse_id is not None else None
    if entry is None:  # settled race but this horse has no result row (post-rec scratch) → void
        return WinRealized(settled=True, hit=None)
    finish_order, status = entry
    if status == ResultStatus.FINISHED and finish_order == 1:
        odds = float(market_odds_used) if market_odds_used is not None else None
        if odds is None:  # settled hit with no recorded odds → cannot value (should not occur)
            return WinRealized(settled=True, hit=True, dead_heat=n_winners > 1)
        return WinRealized(
            settled=True, hit=True, dead_heat=n_winners > 1,
            realized_return=odds, realized_roi=odds - 1.0,
        )
    # finished-but-not-1st, or DNF (stopped/disqualified) → a loss
    return WinRealized(settled=True, hit=False, realized_return=0.0, realized_roi=-1.0)
