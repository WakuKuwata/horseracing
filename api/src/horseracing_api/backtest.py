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
    gross_return: float | None = None  # per-unit payout multiple: real odds if hit else 0.0
    net_return: float | None = None  # gross_return - 1 (odds-1 on hit, -1 on miss)


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
            gross_return=odds, net_return=odds - 1.0,
        )
    # finished-but-not-1st, or DNF (stopped/disqualified) → a loss
    return WinRealized(settled=True, hit=False, gross_return=0.0, net_return=-1.0)


@dataclasses.dataclass(frozen=True)
class FavoriteRealized:
    """Feature 064: retrospective outcome of the market baseline (flat-bet the favorite to win).

    An honest reference line for the decision-support display — NOT a profit strategy. The
    favorite is the started horse with the lowest win odds; realised via the SAME real-odds
    predicate as win_realized. All-null when the race is unsettled or has no priced horse."""

    horse_number: int | None = None
    odds: float | None = None
    settled: bool = False
    hit: bool | None = None
    dead_heat: bool = False
    gross_return: float | None = None
    net_return: float | None = None


def is_prospective(logic_version: str | None) -> bool:
    """Feature 065: EXACT prospective marker parse (codex) — split by ';', not loose contains, so a
    stray substring cannot false-positive. A row is prospective iff it carries the ``prospective=1``
    token that ONLY the live prospective collection path writes."""
    if not logic_version:
        return False
    return "prospective=1" in logic_version.split(";")


@dataclasses.dataclass(frozen=True)
class ShadowLogSummary:
    """Feature 065: read-time roll-up of PROSPECTIVE settled real-win recommendations, valued on the
    FROZEN bet-time odds (never current/closing). Honest instrument — not a profit claim."""

    n_prospective: int = 0           # prospective win rows in scope (settled + pending)
    n_settled: int = 0               # valued (hit True/False) — the ROI/hit denominator
    n_hit: int = 0
    hit_rate: float | None = None
    recovery_rate: float | None = None   # Σ gross_return / n_settled (frozen odds)
    n_pending: int = 0               # marker present but no result yet (excluded from ROI)
    n_void: int = 0                  # settled but hit=None (scratch/void) — excluded from ROI
    weak_pretime: int = 0            # rows whose pre-race guarantee is weak (post_time unknown)
    by_month: list[dict] = dataclasses.field(default_factory=list)  # [{month,n_settled,recovery}]
    first_at: str | None = None
    last_at: str | None = None


def shadow_log_summary(rows) -> ShadowLogSummary:
    """Aggregate PROSPECTIVE win rows (pure, read-only, betting-independent). Each row is a dict:
    ``logic_version, selection, market_odds_used, is_estimated_odds, estimated_market_odds_used,
    bet_type, computed_at, finish_map, n_winners``. Only bet_type==win ∧ exact prospective marker ∧
    real single-win odds (is_estimated_odds False, market_odds_used>0, estimated None) ∧ valid WIN
    dict selection are counted. Valuation uses win_realized on the FROZEN market_odds_used — the
    current race_horses.odds and favorite_realized are NEVER read here (that would be closing).
    Voids (hit=None) and unsettled (pending) are excluded from the ROI/hit denominator."""
    n_prospective = n_settled = n_hit = n_pending = n_void = weak = 0
    total_return = 0.0
    months: dict[str, list[float]] = {}
    stamps: list[str] = []
    for r in rows:
        if r.get("bet_type") != "win" or not is_prospective(r.get("logic_version")):
            continue
        if r.get("is_estimated_odds") or r.get("estimated_market_odds_used") is not None:
            continue
        odds = r.get("market_odds_used")
        sel = r.get("selection")
        if odds is None or float(odds) <= 0.0 or not isinstance(sel, dict):
            continue
        n_prospective += 1
        if "weak_pretime=1" in (r.get("logic_version") or "").split(";"):
            weak += 1
        if r.get("computed_at") is not None:
            stamps.append(str(r["computed_at"]))
        wr = win_realized(sel, odds, finish_map=r.get("finish_map") or {},
                          n_winners=int(r.get("n_winners") or 0))
        if not wr.settled:
            n_pending += 1
            continue
        if wr.hit is None:            # settled but void (scratch etc.) — out of ROI denominator
            n_void += 1
            continue
        n_settled += 1
        ret = wr.gross_return or 0.0
        total_return += ret
        if wr.hit:
            n_hit += 1
        month = str(r.get("computed_at"))[:7] if r.get("computed_at") is not None else "?"
        months.setdefault(month, []).append(ret)
    by_month = [
        {"month": m, "n_settled": len(v), "recovery": (sum(v) / len(v)) if v else None}
        for m, v in sorted(months.items())
    ]
    return ShadowLogSummary(
        n_prospective=n_prospective, n_settled=n_settled, n_hit=n_hit,
        hit_rate=(n_hit / n_settled) if n_settled else None,
        recovery_rate=(total_return / n_settled) if n_settled else None,
        n_pending=n_pending, n_void=n_void, weak_pretime=weak, by_month=by_month,
        first_at=(min(stamps) if stamps else None), last_at=(max(stamps) if stamps else None),
    )


def favorite_realized(
    odds_rows, *, finish_map: dict[str, tuple[int | None, str]], n_winners: int
) -> FavoriteRealized:
    """Compute the favorite's realised WIN outcome (pure, read-only, betting-independent).

    ``odds_rows`` = iterable of rows whose first three fields are (horse_number, horse_id, odds)
    for started horses (queries.win_odds yields a trailing updated_at, ignored here). The favorite
    = the row with the lowest positive odds. Delegates to win_realized so the hit/void/dead-heat/
    return semantics match the recommendation rows. Display-only; never a feature (II)."""
    priced = [
        (row[0], row[1], float(row[2]))
        for row in odds_rows if row[2] is not None and float(row[2]) > 0.0
    ]
    if not priced:
        return FavoriteRealized()
    hn, hid, odds = min(priced, key=lambda r: r[2])
    wr = win_realized(
        {"horse_id": hid, "horse_number": hn}, odds, finish_map=finish_map, n_winners=n_winners
    )
    return FavoriteRealized(
        horse_number=hn, odds=odds, settled=wr.settled, hit=wr.hit, dead_heat=wr.dead_heat,
        gross_return=wr.gross_return, net_return=wr.net_return,
    )
