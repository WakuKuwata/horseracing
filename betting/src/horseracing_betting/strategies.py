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


class OddsCappedEVStrategy:
    """Feature 064: EV>=threshold bets restricted to odds < odds_cap (win upper cap).

    Used by the adoption gate to compare the current EV policy against the odds-capped policy on
    the SAME OOS race set. Delegates to select_ev_bets(odds_cap=) so the denominator/renorm is
    identical to EVStrategy (capped horses stay in the probability denominator).
    """

    def __init__(self, threshold: float, odds_cap: float) -> None:
        self.threshold = threshold
        self.odds_cap = odds_cap
        self.name = f"ev_oddscap{int(odds_cap)}"

    def bets_for_race(self, horses: list[dict], *, stake: float) -> list[Bet]:
        return select_ev_bets(
            horses, threshold=self.threshold, stake=stake, odds_cap=self.odds_cap
        )


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
