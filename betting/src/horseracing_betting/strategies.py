"""Betting strategies for the pseudo-ROI backtest (contracts/backtest.md).

EVStrategy bets EV>=threshold horses (uses renormalized win_prob). The two ROI baselines are
odds-only and exist specifically for ROI comparison — they are NOT the probability-quality
baselines of Feature 003 (codex). All flat per-bet stake; all bet only started + valid-odds
horses on the SAME race set (the caller fixes the race list).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .ev import Bet, eligible_started, renormalized_started_probs, select_ev_bets


@runtime_checkable
class Strategy(Protocol):
    name: str
    def bets_for_race(self, horses: list[dict], *, stake: float) -> list[Bet]: ...


class EVStrategy:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        self.name = "ev"

    def bets_for_race(self, horses: list[dict], *, stake: float) -> list[Bet]:
        return select_ev_bets(horses, threshold=self.threshold, stake=stake)


class FavoriteROIBaseline:
    """Always bet the favorite (lowest odds) to win, flat stake."""

    name = "favorite"

    def bets_for_race(self, horses: list[dict], *, stake: float) -> list[Bet]:
        elig = eligible_started(horses)
        if not elig:
            return []
        fav = min(elig, key=lambda h: float(h["odds"]))
        probs = renormalized_started_probs(horses)
        p = probs.get(fav["horse_id"])
        odds = float(fav["odds"])
        return [Bet(fav["horse_id"], fav.get("horse_number"), p, odds,
                    (p * odds if p is not None else None), stake)]


class UniformROIBaseline:
    """Bet every started, valid-odds horse to win, flat stake each."""

    name = "uniform"

    def bets_for_race(self, horses: list[dict], *, stake: float) -> list[Bet]:
        probs = renormalized_started_probs(horses)
        out: list[Bet] = []
        for h in eligible_started(horses):
            odds = float(h["odds"])
            p = probs.get(h["horse_id"])
            out.append(Bet(h["horse_id"], h.get("horse_number"), p, odds,
                           (p * odds if p is not None else None), stake))
        return out
