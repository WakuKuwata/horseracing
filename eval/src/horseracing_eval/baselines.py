"""Baseline predictors (research R6/R7).

- MarketBaseline: win = normalized 1/odds (implied prob), top2/top3 via Harville.
  Uses result-time odds -> reference-only / leaky (FR-013, is_leaky_reference=True).
- UniformBaseline: win=1/N, top2/top3 capped. Truly leak-free floor.
"""

from __future__ import annotations

from .predictor import Prediction, RaceContext

_EPS = 1e-12


def harville_topk(win: list[float]) -> tuple[list[float], list[float]]:
    """Derive P(top2)/P(top3) from win probs (Plackett-Luce / Harville).

    Public so feature-based predictors (Feature 005) derive top2/top3 identically
    to the market baseline (research R8). The race-normalized win vector must sum
    to ~1 for the result to satisfy probability consistency.
    """
    n = len(win)
    top2 = [0.0] * n
    top3 = [0.0] * n
    for i in range(n):
        t2 = win[i]
        for j in range(n):
            if j == i:
                continue
            dj = 1.0 - win[j]
            if dj > _EPS:
                t2 += win[j] * win[i] / dj
        top2[i] = min(t2, 1.0)

        t3 = top2[i]
        for j in range(n):
            if j == i:
                continue
            dj = 1.0 - win[j]
            if dj <= _EPS:
                continue
            for k in range(n):
                if k == i or k == j:
                    continue
                djk = 1.0 - win[j] - win[k]
                if djk <= _EPS:
                    continue
                t3 += win[j] * (win[k] / dj) * (win[i] / djk)
        top3[i] = min(max(t3, top2[i]), 1.0)
    return top2, top3


class UniformBaseline:
    """Leak-free floor: win=1/N, top2=min(2/N,1), top3=min(3/N,1)."""

    is_leaky_reference = False

    def fit(self, train_races: list[RaceContext]) -> None:  # noqa: ARG002
        return None

    def predict_race(self, race: RaceContext) -> dict[str, Prediction]:
        n = len(race.started_horses)
        win = 1.0 / n
        top2 = min(2.0 / n, 1.0)
        top3 = min(3.0 / n, 1.0)
        pred = Prediction(win=win, top2=top2, top3=top3)
        return {h.horse_id: pred for h in race.started_horses}


class MarketBaseline:
    """市場参照線 (人気順). 結果確定 odds を使うため参照線専用 (FR-013)."""

    is_leaky_reference = True

    def fit(self, train_races: list[RaceContext]) -> None:  # noqa: ARG002
        return None

    def predict_race(self, race: RaceContext) -> dict[str, Prediction]:
        horses = race.started_horses
        implied: list[float | None] = []
        for h in horses:
            odds = h.result_market.odds if h.result_market else None
            implied.append(1.0 / odds if (odds is not None and odds > 0) else None)

        valid = [x for x in implied if x is not None]
        floor = min(valid) * 0.01 if valid else 1.0
        weights = [x if x is not None else floor for x in implied]
        total = sum(weights)
        win = [w / total for w in weights]
        top2, top3 = harville_topk(win)
        return {
            h.horse_id: Prediction(win=win[i], top2=top2[i], top3=top3[i])
            for i, h in enumerate(horses)
        }
